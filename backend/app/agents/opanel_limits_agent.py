#!/usr/bin/env python3
"""Resource limits agent: CPU, memory, processes and disk I/O per hosting account.

Installed by the "Resource limits" addon and run by systemd as root
(opanel-limits.service). The panel never talks to it directly: it writes
/etc/opanel-limits/config.json through the root helper, and reads the usage
this agent writes to /run/opanel-limits/usage.json and
/var/lib/opanel-limits/history.json.

How it works (plan of 2026-10-04, tested on .41 and .88 first):

- Every account has a systemd slice, nested under its reseller's when it has
  one, so a reseller's cap bounds all its customers together:
  hosting.slice > hosting-r5.slice > hosting-r5-a12.slice. The limits are
  runtime properties of those slices (CPUQuota, MemoryHigh/MemoryMax,
  TasksMax, IO*BandwidthMax), set again whenever the agent starts.
- A site's PHP does not start in its owner's slice: OpenLiteSpeed's lsphp and
  PHP-FPM's workers are children of the web server, cron jobs of cron, the
  panel's file work of the panel. So the agent moves them. The kernel's
  process connector reports the moment a process takes on a hosting
  account's uid (setuid in lscgid, a PHP-FPM worker, cron, sudo); the
  process goes into a scope in the account's slice, and everything it forks
  after that is born there. A scan every 30 seconds catches what the events
  missed. Only processes coming from the shared services listed in
  `sources` are moved: a unit of the account's own (an application) keeps
  its process, and SSH logins stay in their logind session, whose
  user-<uid>.slice gets the same limits.
- Scopes are delegated (systemd only lets a process be attached to a
  delegated unit), keep running when one of their processes is OOM-killed
  (OOMPolicy=continue) and are collected when empty.

Nothing here is a security boundary: a process runs in its default cgroup
for the few milliseconds before it is moved. It is a fair-share mechanism,
which is all CloudLinux's LVE is too.
"""

import fnmatch
import json
import os
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import time

VERSION = "1"
CONFIG_FILE = os.environ.get("LIMITS_CONFIG", "/etc/opanel-limits/config.json")
RUN_DIR = os.environ.get("LIMITS_RUN_DIR", "/run/opanel-limits")
STATE_DIR = os.environ.get("LIMITS_STATE_DIR", "/var/lib/opanel-limits")
CGROUP_ROOT = "/sys/fs/cgroup"

SAMPLE_SECONDS = 10
RECONCILE_SECONDS = 30
CONFIG_CHECK_SECONDS = 2
HISTORY_SECONDS = 300
HISTORY_DAY_POINTS = 288      # 24 h of 5-minute points
HISTORY_WEEK_POINTS = 168     # 7 days of hourly points

GROUP_SLICE_RE = re.compile(r"^hosting-r[0-9]{1,9}\.slice$")
ACCOUNT_SLICE_RE = re.compile(r"^hosting(-r[0-9]{1,9})?-a[0-9]{1,9}\.slice$")
SOURCE_RE = re.compile(r"^[A-Za-z0-9@._*-]{1,80}\.service$")
LIMIT_BOUNDS = {
    "cpu_percent": 100000,      # 1000 cores
    "memory_mb": 16 * 1024 * 1024,
    "process_limit": 1000000,
    "io_read_mbps": 1000000,
    "io_write_mbps": 1000000,
}
DEFAULT_SOURCES = ["lshttpd.service", "php*-fpm.service", "cron.service", "ssh.service"]

# Kernel process connector.
NETLINK_CONNECTOR = 11
CN_IDX_PROC = 1
PROC_CN_MCAST_LISTEN = 1
PROC_EVENT_UID = 0x00000004

_stopping = False


def log(message):
    print(message, flush=True)


# --- configuration -----------------------------------------------------------------

def clean_limits(raw):
    limits = {}
    for key, bound in LIMIT_BOUNDS.items():
        value = (raw or {}).get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > bound:
            raise ValueError(f"{key} must be a whole number from 0 to {bound}")
        limits[key] = value
    return limits


def load_config(path=CONFIG_FILE):
    """Read and validate the panel's configuration. Anything malformed is
    refused whole: half a configuration would move processes to the wrong place."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("unsupported configuration version")
    io_path = data.get("io_path") or "/home"
    if not isinstance(io_path, str) or not io_path.startswith("/") or not re.match(r"^/[A-Za-z0-9/._-]*$", io_path):
        raise ValueError("io_path must be an absolute path")
    sources = data.get("sources") or DEFAULT_SOURCES
    if not isinstance(sources, list) or not all(isinstance(s, str) and SOURCE_RE.match(s) for s in sources):
        raise ValueError("sources must be service names")
    groups = {}
    for item in data.get("groups") or []:
        name = item.get("slice")
        if not isinstance(name, str) or not GROUP_SLICE_RE.match(name):
            raise ValueError(f"bad group slice: {name!r}")
        groups[name] = clean_limits(item.get("limits"))
    accounts = {}
    uids = {}
    for item in data.get("accounts") or []:
        name = item.get("slice")
        if not isinstance(name, str) or not ACCOUNT_SLICE_RE.match(name):
            raise ValueError(f"bad account slice: {name!r}")
        account_uids = item.get("uids") or []
        if not isinstance(account_uids, list) or not all(isinstance(u, int) and 1000 <= u < 60000 for u in account_uids):
            raise ValueError(f"bad uids for {name}")
        for uid in account_uids:
            if uid in uids and uids[uid] != name:
                raise ValueError(f"uid {uid} belongs to two accounts")
            uids[uid] = name
        accounts[name] = {"uids": sorted(set(account_uids)), "limits": clean_limits(item.get("limits")),
                          "sessions": bool(item.get("sessions", True))}
    return {"io_path": io_path, "sources": sources, "groups": groups, "accounts": accounts, "uids": uids}


EMPTY_CONFIG = {"io_path": "/home", "sources": DEFAULT_SOURCES, "groups": {}, "accounts": {}, "uids": {}}


# --- slices: where the limits live ----------------------------------------------------

def slice_path(name):
    """hosting-r5-a12.slice -> hosting.slice/hosting-r5.slice/hosting-r5-a12.slice"""
    parts = name[: -len(".slice")].split("-")
    return "/".join("-".join(parts[: i + 1]) + ".slice" for i in range(len(parts)))


def scope_name(slice_name, uid):
    return f"limits-{slice_name[: -len('.slice')]}-u{uid}.scope"


def properties(limits, io_path):
    """systemd properties for a set of limits; 0 means unlimited."""
    cpu = limits["cpu_percent"]
    memory = limits["memory_mb"]
    tasks = limits["process_limit"]
    read = limits["io_read_mbps"]
    write = limits["io_write_mbps"]
    props = [
        f"CPUQuota={cpu}%" if cpu else "CPUQuota=",
        # High throttles and reclaims before Max kills: a site slows down
        # rather than failing, unless it really is past its limit.
        f"MemoryHigh={max(1, memory * 9 // 10)}M" if memory else "MemoryHigh=infinity",
        f"MemoryMax={memory}M" if memory else "MemoryMax=infinity",
        # Swap would let a limited account page the whole server to death.
        "MemorySwapMax=0" if memory else "MemorySwapMax=infinity",
        f"TasksMax={tasks}" if tasks else "TasksMax=infinity",
        "IOReadBandwidthMax=",
        "IOWriteBandwidthMax=",
    ]
    if read:
        props.append(f"IOReadBandwidthMax={io_path} {read}M")
    if write:
        props.append(f"IOWriteBandwidthMax={io_path} {write}M")
    return props


UNLIMITED = {key: 0 for key in LIMIT_BOUNDS}


def set_properties(unit, props):
    result = subprocess.run(["systemctl", "set-property", "--runtime", unit] + props,
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        log(f"could not set limits on {unit}: {(result.stderr or result.stdout).strip()}")
        return False
    return True


class Applier:
    """Applies limits to slices, remembering what it set so a change or a
    removal touches only what moved, and a release can undo all of it."""

    def __init__(self):
        self.applied = self._load()

    def _load(self):
        try:
            with open(os.path.join(STATE_DIR, "applied.json"), encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = os.path.join(STATE_DIR, "applied.json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.applied, handle, sort_keys=True)
        os.replace(tmp, os.path.join(STATE_DIR, "applied.json"))

    def apply(self, config, force=False):
        wanted = {}
        for name, limits in config["groups"].items():
            wanted[name] = properties(limits, config["io_path"])
        for name, account in config["accounts"].items():
            props = properties(account["limits"], config["io_path"])
            wanted[name] = props
            if account["sessions"]:
                for uid in account["uids"]:
                    wanted[f"user-{uid}.slice"] = props
        unlimited = properties(UNLIMITED, config["io_path"])
        for unit in list(self.applied):
            if unit not in wanted:
                if set_properties(unit, unlimited):
                    self.applied.pop(unit, None)
        for unit, props in wanted.items():
            if force or self.applied.get(unit) != props:
                if set_properties(unit, props):
                    self.applied[unit] = props
        self._save()

    def release(self, io_path="/home"):
        unlimited = properties(UNLIMITED, io_path)
        units = set(self.applied)
        base = os.path.join(CGROUP_ROOT, "hosting.slice")
        for root, dirs, _files in os.walk(base):
            units.update(d for d in dirs if d.endswith(".slice"))
        for unit in sorted(units):
            set_properties(unit, unlimited)
        self.applied = {}
        self._save()


# --- processes ---------------------------------------------------------------------

def read_uid(pid):
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("Uid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def read_cgroup(pid):
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("0::"):
                    return line[3:].strip()
    except OSError:
        return None
    return None


def movable(cgroup, sources, target_scope):
    """Whether a process in this cgroup should go to target_scope.

    Shared services listed in `sources` (the web server, PHP-FPM, cron, the
    panel) and another of our own scopes (the account moved to a different
    reseller) are fair game. Everything else is left alone: an application's
    own unit keeps its process, and a login keeps its logind session.
    """
    if not cgroup or cgroup.endswith("/" + target_scope):
        return False
    leaf = cgroup.rsplit("/", 1)[-1]
    if cgroup.startswith("/hosting.slice/") and leaf.startswith("limits-") and leaf.endswith(".scope"):
        return True
    if not cgroup.startswith("/system.slice/"):
        return False
    service = cgroup[len("/system.slice/"):].split("/", 1)[0]
    return any(fnmatch.fnmatchcase(service, pattern) for pattern in sources)


def busctl(*args):
    return subprocess.run(["busctl", "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                           "org.freedesktop.systemd1.Manager"] + list(args),
                          capture_output=True, text=True, timeout=30)


def attach(scope, slice_name, pids):
    """Put pids into scope, creating the scope (in slice_name) if it is not running."""
    pids = [str(p) for p in pids]
    error = ""
    # Attach to the running scope; failing that, start it with these pids;
    # failing that (somebody started it in between), attach again.
    for step in ("attach", "start", "attach"):
        if step == "attach":
            result = busctl("AttachProcessesToUnit", "ssau", scope, "", str(len(pids)), *pids)
        else:
            result = busctl("StartTransientUnit", "ssa(sv)a(sa(sv))", scope, "fail", "5",
                            "PIDs", "au", str(len(pids)), *pids,
                            "Slice", "s", slice_name,
                            # systemd only attaches processes to a delegated unit.
                            "Delegate", "b", "true",
                            # One PHP worker killed for memory must not end the scope.
                            "OOMPolicy", "s", "continue",
                            "CollectMode", "s", "inactive-or-failed",
                            "0")
        if result.returncode == 0:
            return True
        error = (result.stderr or result.stdout).strip()
        if "No such process" in error or "ESRCH" in error:
            break
    if len(pids) > 1:
        # One of them may simply have exited; do the rest one by one.
        return any(attach(scope, slice_name, [pid]) for pid in pids)
    if "No such process" not in error and "ESRCH" not in error:
        log(f"could not move {pids} to {scope}: {error}")
    return False


class Mover:
    def __init__(self):
        self.pending = set()

    def note(self, pid):
        self.pending.add(pid)

    def flush(self, config):
        pids, self.pending = self.pending, set()
        self.move(config, pids)

    def move(self, config, pids):
        by_scope = {}
        for pid in pids:
            uid = read_uid(pid)
            slice_name = config["uids"].get(uid)
            if not slice_name:
                continue
            scope = scope_name(slice_name, uid)
            if movable(read_cgroup(pid), config["sources"], scope):
                by_scope.setdefault((scope, slice_name), []).append(pid)
        for (scope, slice_name), batch in by_scope.items():
            attach(scope, slice_name, batch)

    def reconcile(self, config):
        if not config["uids"]:
            return
        pids = [int(name) for name in os.listdir("/proc") if name.isdigit()]
        self.move(config, pids)


def open_connector():
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_CONNECTOR)
    sock.bind((os.getpid(), CN_IDX_PROC))
    op = struct.pack("=I", PROC_CN_MCAST_LISTEN)
    cn = struct.pack("=IIIIHH", CN_IDX_PROC, 1, 0, 0, len(op), 0) + op
    sock.send(struct.pack("=IHHII", 16 + len(cn), 3, 0, 0, os.getpid()) + cn)
    sock.setblocking(False)
    return sock


def uid_events(sock, uids):
    """pids that just took on a hosting uid, from whatever the socket holds."""
    found = []
    while True:
        try:
            data = sock.recv(65536)
        except BlockingIOError:
            return found
        except OSError as exc:
            # ENOBUFS: the kernel dropped events. The next scan picks them up.
            log(f"process events: {exc}")
            return found
        offset = 0
        while offset + 16 <= len(data):
            length = struct.unpack_from("=I", data, offset)[0]
            if length < 16:
                break
            body = offset + 16 + 20
            if body + 16 + 16 <= offset + length:
                what = struct.unpack_from("=I", data, body)[0]
                if what == PROC_EVENT_UID:
                    _pid, tgid, ruid, euid = struct.unpack_from("=iiII", data, body + 16)
                    if ruid in uids or euid in uids:
                        found.append(tgid)
            offset += (length + 3) & ~3


# --- usage ---------------------------------------------------------------------------

def read_kv(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) == 2:
                    values[parts[0]] = int(parts[1])
    except (OSError, ValueError):
        pass
    return values


def read_int(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
        return 0 if value == "max" else int(value)
    except (OSError, ValueError):
        return 0


def read_io(path):
    read = write = 0
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                for field in line.split()[1:]:
                    key, _, value = field.partition("=")
                    if key == "rbytes":
                        read += int(value)
                    elif key == "wbytes":
                        write += int(value)
    except (OSError, ValueError):
        pass
    return read, write


def counters(slice_name):
    base = os.path.join(CGROUP_ROOT, slice_path(slice_name))
    if not os.path.isdir(base):
        return None
    cpu = read_kv(os.path.join(base, "cpu.stat"))
    events = read_kv(os.path.join(base, "memory.events"))
    read, write = read_io(os.path.join(base, "io.stat"))
    return {
        "cpu_usec": cpu.get("usage_usec", 0),
        "throttled_usec": cpu.get("throttled_usec", 0),
        "memory_bytes": read_int(os.path.join(base, "memory.current")),
        "processes": read_int(os.path.join(base, "pids.current")),
        "read_bytes": read,
        "write_bytes": write,
        "memory_high": events.get("high", 0),
        "memory_max": events.get("max", 0),
        "oom_kills": events.get("oom_kill", 0),
    }


def write_json(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


class Sampler:
    def __init__(self):
        self.last = {}
        self.window = {}
        self.history = self._load_history()

    def _load_history(self):
        try:
            with open(os.path.join(STATE_DIR, "history.json"), encoding="utf-8") as handle:
                data = json.load(handle)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def sample(self, config):
        now = time.time()
        usage = {}
        names = list(config["groups"]) + list(config["accounts"])
        for name in names:
            current = counters(name)
            if current is None:
                usage[name] = {"cpu_percent": 0.0, "memory_mb": 0, "processes": 0, "io_read_mbps": 0.0,
                               "io_write_mbps": 0.0, "memory_high_events": 0, "memory_max_events": 0,
                               "oom_kills": 0, "throttled_seconds": 0.0}
                continue
            previous = self.last.get(name)
            self.last[name] = (now, current)
            cpu = read_mb = write_mb = 0.0
            if previous:
                elapsed = max(0.001, now - previous[0])
                before = previous[1]
                cpu = max(0.0, (current["cpu_usec"] - before["cpu_usec"]) / (elapsed * 10000.0))
                read_mb = max(0.0, (current["read_bytes"] - before["read_bytes"]) / elapsed / 1048576)
                write_mb = max(0.0, (current["write_bytes"] - before["write_bytes"]) / elapsed / 1048576)
            usage[name] = {
                "cpu_percent": round(cpu, 1),
                "memory_mb": current["memory_bytes"] // 1048576,
                "processes": current["processes"],
                # To the KB/s: a quiet site moves a few KB a second, which
                # two decimals of MB/s would round away to 0.
                "io_read_mbps": round(read_mb, 3),
                "io_write_mbps": round(write_mb, 3),
                # Totals since the slice was made; the panel shows differences.
                "memory_high_events": current["memory_high"],
                "memory_max_events": current["memory_max"],
                "oom_kills": current["oom_kills"],
                "throttled_seconds": round(current["throttled_usec"] / 1e6, 1),
            }
            bucket = self.window.setdefault(name, [])
            bucket.append(usage[name])
        write_json(os.path.join(RUN_DIR, "usage.json"), {"time": int(now), "interval": SAMPLE_SECONDS,
                                                         "slices": usage})
        return usage

    def roll_history(self, config):
        """Every five minutes: one averaged point per slice, kept for a day;
        every hour, one more kept for a week."""
        now = int(time.time())
        names = set(config["groups"]) | set(config["accounts"])
        for name in list(self.history):
            if name not in names:
                self.history.pop(name)
        for name in names:
            points = self.window.pop(name, [])
            if not points:
                continue
            last = points[-1]
            point = {
                "t": now,
                "cpu": round(sum(p["cpu_percent"] for p in points) / len(points), 1),
                "cpu_peak": max(p["cpu_percent"] for p in points),
                "mem": max(p["memory_mb"] for p in points),
                "procs": max(p["processes"] for p in points),
                "io_r": round(sum(p["io_read_mbps"] for p in points) / len(points), 3),
                "io_w": round(sum(p["io_write_mbps"] for p in points) / len(points), 3),
                "oom": last["oom_kills"],
                "mem_max": last["memory_max_events"],
            }
            entry = self.history.setdefault(name, {"day": [], "week": []})
            entry["day"] = (entry["day"] + [point])[-HISTORY_DAY_POINTS:]
            week = entry["week"]
            if not week or now - week[-1]["t"] >= 3600:
                hour = [p for p in entry["day"] if p["t"] > now - 3600] or [point]
                week.append({
                    "t": now,
                    "cpu": round(sum(p["cpu"] for p in hour) / len(hour), 1),
                    "cpu_peak": max(p["cpu_peak"] for p in hour),
                    "mem": max(p["mem"] for p in hour),
                    "procs": max(p["procs"] for p in hour),
                    "io_r": round(sum(p["io_r"] for p in hour) / len(hour), 3),
                    "io_w": round(sum(p["io_w"] for p in hour) / len(hour), 3),
                    "oom": point["oom"],
                    "mem_max": point["mem_max"],
                })
                entry["week"] = week[-HISTORY_WEEK_POINTS:]
        write_json(os.path.join(STATE_DIR, "history.json"), self.history)


# --- main -----------------------------------------------------------------------------

def config_mtime():
    try:
        return os.stat(CONFIG_FILE).st_mtime_ns
    except OSError:
        return None


def read_config_or_keep(current):
    try:
        return load_config()
    except FileNotFoundError:
        return EMPTY_CONFIG
    except (OSError, ValueError) as exc:
        log(f"configuration refused, keeping the previous one: {exc}")
        return current


def stop(_signum, _frame):
    global _stopping
    _stopping = True


def run():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    applier = Applier()
    mover = Mover()
    sampler = Sampler()
    config = read_config_or_keep(EMPTY_CONFIG)
    mtime = config_mtime()
    # Runtime properties do not survive a reboot: set them all again.
    applier.apply(config, force=True)
    mover.reconcile(config)
    try:
        sock = open_connector()
    except OSError as exc:
        log(f"process events unavailable ({exc}); relying on the {RECONCILE_SECONDS}s scan")
        sock = None
    log(f"opanel-limits {VERSION}: {len(config['accounts'])} accounts, {len(config['groups'])} reseller groups")
    now = time.monotonic()
    next_config = now + CONFIG_CHECK_SECONDS
    next_reconcile = now + RECONCILE_SECONDS
    next_sample = now
    next_history = now + HISTORY_SECONDS
    flush_at = None
    while not _stopping:
        timeout = 1.0 if flush_at is None else max(0.0, flush_at - time.monotonic())
        if sock is not None:
            ready, _w, _x = select.select([sock], [], [], timeout)
            if ready:
                for pid in uid_events(sock, config["uids"]):
                    mover.note(pid)
                    if flush_at is None:
                        # A short batch: PHP-FPM starts several workers at once.
                        flush_at = time.monotonic() + 0.05
        else:
            time.sleep(timeout)
        now = time.monotonic()
        if flush_at is not None and now >= flush_at:
            mover.flush(config)
            flush_at = None
        if now >= next_config:
            next_config = now + CONFIG_CHECK_SECONDS
            current = config_mtime()
            if current != mtime:
                mtime = current
                config = read_config_or_keep(config)
                applier.apply(config)
                mover.reconcile(config)
                log(f"configuration reloaded: {len(config['accounts'])} accounts")
        if now >= next_reconcile:
            next_reconcile = now + RECONCILE_SECONDS
            mover.reconcile(config)
        if now >= next_sample:
            next_sample = now + SAMPLE_SECONDS
            try:
                sampler.sample(config)
            except OSError as exc:
                log(f"usage not written: {exc}")
        if now >= next_history:
            next_history = now + HISTORY_SECONDS
            try:
                sampler.roll_history(config)
            except OSError as exc:
                log(f"history not written: {exc}")
    log("stopping; limits stay in place until released or the next boot")


def main(argv):
    if len(argv) > 1 and argv[1] == "--version":
        print(VERSION)
        return 0
    if len(argv) > 2 and argv[1] == "--check":
        try:
            config = load_config(argv[2])
        except (OSError, ValueError) as exc:
            print(f"invalid: {exc}", file=sys.stderr)
            return 1
        print(f"ok: {len(config['accounts'])} accounts, {len(config['groups'])} groups")
        return 0
    if len(argv) > 1 and argv[1] == "--release":
        # The addon is being stopped or removed: every limit goes, processes
        # stay where they are (an unlimited slice is no different from none).
        Applier().release()
        print("released")
        return 0
    if os.geteuid() != 0:
        print("must run as root", file=sys.stderr)
        return 1
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
