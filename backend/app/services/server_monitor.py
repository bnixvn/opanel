"""The server's own pages, reached from the status chips in the top bar
(operator, 2026-10-06): Process monitor (top -c), RAM usage, Disk usage and
Traffic. Administrators only -- a process list shows every tenant's command
lines, and the rest is the server's, not an account's.

The panel's API cannot see other users' processes (its unit has
ProtectProc=invisible), so top runs as root through the helper. So does the
disk scan: du has to read every account's tree. The scan can take minutes on
a large /home, so it runs in the background and its last result is kept.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from app.services.shell import shell

logger = logging.getLogger("opanel.server_monitor")

DISK_CACHE = Path("/var/lib/opanel/disk-usage.json")
# A scan older than this is refreshed when the page is opened.
DISK_STALE_SECONDS = 6 * 3600
SKIP_FS_TYPES = {"tmpfs", "devtmpfs", "squashfs", "overlay", "efivarfs", "ramfs", "nsfs", "fuse.snapfuse"}

_scan_lock = threading.Lock()
_scan_state = {"running": False, "started": 0.0, "error": ""}


# --- Process monitor -----------------------------------------------------------------

def processes() -> dict:
    """One frame of `top -c`, as the server's root sees it."""
    result = shell.privileged("process-top", check=False, fallback=["top", "-b", "-n", "1", "-c", "-w", "300"])
    if result.returncode != 0 and not (result.stdout or "").strip():
        raise RuntimeError((result.stderr or "top failed").strip()[-300:])
    return {"output": result.stdout or "", "time": int(time.time())}


# --- Traffic ---------------------------------------------------------------------------

def traffic() -> dict:
    """Bytes and packets through each network interface since boot. The page
    turns two readings into a rate."""
    interfaces = []
    try:
        lines = Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]
    except OSError:
        lines = []
    for line in lines:
        name, _, data = line.partition(":")
        name = name.strip()
        fields = data.split()
        if name == "lo" or len(fields) < 16:
            continue
        interfaces.append({
            "name": name,
            "rx_bytes": int(fields[0]), "rx_packets": int(fields[1]), "rx_errors": int(fields[2]),
            "tx_bytes": int(fields[8]), "tx_packets": int(fields[9]), "tx_errors": int(fields[10]),
        })
    try:
        uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        uptime = 0.0
    return {"interfaces": interfaces, "time": time.time(), "uptime_seconds": int(uptime)}


# --- Disk usage ------------------------------------------------------------------------

def filesystems() -> list[dict]:
    """Every real filesystem, as df sees it."""
    try:
        completed = subprocess.run(["df", "-PT", "-B1"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in completed.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 7 or parts[1] in SKIP_FS_TYPES or not parts[0].startswith("/"):
            continue
        try:
            size, used, available = int(parts[2]), int(parts[3]), int(parts[4])
        except ValueError:
            continue
        mount = " ".join(parts[6:])
        try:
            dev = str(os.stat(mount).st_dev)
        except OSError:
            dev = ""
        rows.append({"device": parts[0], "type": parts[1], "size": size, "used": used,
                     "available": available, "mount": mount, "dev": dev})
    return rows


def _read_cache() -> dict:
    try:
        data = json.loads(DISK_CACHE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _parse_scan(output: str) -> list[dict]:
    """The helper's lines (key, bytes, path, device number), with the backups
    kept under /home taken out of the websites so nothing is counted twice."""
    lines = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) == 4 and fields[1].isdigit():
            lines.append({"key": fields[0], "bytes": int(fields[1]), "path": fields[2], "dev": fields[3]})
    inside_home = sum(row["bytes"] for row in lines
                      if row["key"] == "backups" and row["path"].startswith("/home/"))
    parts: dict[tuple[str, str], int] = {}
    for row in lines:
        key = "websites" if row["key"] == "home" else row["key"]
        size = max(0, row["bytes"] - inside_home) if key == "websites" else row["bytes"]
        parts[(row["dev"], key)] = parts.get((row["dev"], key), 0) + size
    return [{"dev": dev, "key": key, "bytes": size} for (dev, key), size in parts.items()]


def _account_usage() -> list[dict]:
    from app.core.database import SessionLocal
    from app.models.entities import User
    from app.services import storage_quota

    rows = []
    with SessionLocal() as db:
        for user in db.query(User).order_by(User.id).all():
            try:
                used = storage_quota.user_storage_used_bytes(db, user, use_cache=False)
            except Exception:  # noqa: BLE001 - one account must not lose the rest
                continue
            if used:
                rows.append({"username": user.username, "role": user.role, "bytes": used,
                             "limit_mb": int(getattr(user, "storage_limit_mb", 0) or 0)})
    rows.sort(key=lambda row: row["bytes"], reverse=True)
    return rows[:20]


def _scan() -> None:
    try:
        result = shell.privileged("disk-usage-scan", check=False, fallback=["true"])
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "disk-usage-scan failed").strip()[-300:])
        data = {"time": int(time.time()), "parts": _parse_scan(result.stdout or ""), "accounts": _account_usage()}
        DISK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DISK_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(DISK_CACHE)
        _scan_state["error"] = ""
    except Exception as exc:  # noqa: BLE001 - the page says what went wrong
        logger.warning("Disk usage scan failed", exc_info=True)
        _scan_state["error"] = str(exc)[:300]
    finally:
        _scan_state["running"] = False


def start_scan() -> bool:
    """Start a scan in the background unless one is running."""
    with _scan_lock:
        if _scan_state["running"]:
            return False
        _scan_state.update(running=True, started=time.time(), error="")
    threading.Thread(target=_scan, name="opanel-disk-scan", daemon=True).start()
    return True


def disk(auto_scan: bool = True) -> dict:
    cache = _read_cache()
    stale = not cache or time.time() - int(cache.get("time") or 0) > DISK_STALE_SECONDS
    if auto_scan and stale:
        start_scan()
    systems = filesystems()
    parts = (cache or {}).get("parts") or []
    for fs in systems:
        mine = [part for part in parts if fs["dev"] and part.get("dev") == fs["dev"]]
        # Whatever the scan did not name: the system, packages, swap files.
        known = sum(int(part.get("bytes") or 0) for part in mine)
        fs["parts"] = mine + ([{"dev": fs["dev"], "key": "other", "bytes": fs["used"] - known}]
                              if mine and fs["used"] > known else [])
    return {
        "filesystems": systems,
        "scan": cache or None,
        "scanning": _scan_state["running"],
        "scan_error": _scan_state["error"],
    }


# --- RAM ------------------------------------------------------------------------------

def memory() -> dict:
    """The server's RAM, where it is, swap, and what MariaDB was told to take."""
    from app.services import php_workers, system

    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, raw = line.partition(":")
            parts = raw.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
    except OSError:
        pass
    usage = system._memory_usage() if values else {}
    return {
        "memory": usage,
        "swap": {"total": values.get("SwapTotal", 0), "used": max(0, values.get("SwapTotal", 0) - values.get("SwapFree", 0))},
        "mariadb_pool_mb": php_workers.mariadb_pool_mb(),
        "php_workers": php_workers.summary(),
    }

