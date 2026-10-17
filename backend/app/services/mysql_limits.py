"""MariaDB's share of the server, and who is using it (as SNPanel's MySQL limits, 2026-10-17).

A customer's queries run in MariaDB's threads, in mariadb.service's cgroup -
outside every account's own limits. So the whole of MariaDB gets a budget:
CPU, IO and memory on its unit (`systemctl set-property` through the helper),
and a per-account connection backstop (`max_user_connections`, with the
statistics `userstat` feeds), written to an option file for the next start
and set on the running server. The page then shows MariaDB's use now, the
busiest database users, and the statements running, which an administrator
may stop.

The settings are kept in panel-settings.json under "mysql_limits".
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from app.services import mariadb, panel_settings
from app.services.shell import shell

SETTING = "mysql_limits"
FIELDS = ("cpu_quota_percent", "cpu_weight", "io_weight", "memory_max_mb", "max_user_connections")
DEFAULTS = {"cpu_quota_percent": 0, "cpu_weight": 100, "io_weight": 100, "memory_max_mb": 0,
            "max_user_connections": 0, "userstat": True}
CGROUP = Path("/sys/fs/cgroup/system.slice/mariadb.service")
SAMPLE_SECONDS = 1.0
# Threads that are MariaDB's own, and accounts nobody stops from here.
SYSTEM_USERS = {"system user", "event_scheduler", "unauthenticated user", "root", "opanel", "debian-sys-maint", "mysql"}
INFO_CHARS = 300


def ranges() -> dict:
    cores = os.cpu_count() or 1
    return {
        "cpu_quota_percent": [0, cores * 100],
        "cpu_weight": [1, 10000],
        "io_weight": [1, 10000],
        "memory_max_mb": [0, 1024 * 1024],
        "max_user_connections": [0, 10000],
    }


def buffer_pool_mb() -> int | None:
    try:
        rows = mariadb._rows("SELECT @@innodb_buffer_pool_size;")
        return int(int(rows[0][0]) / 1048576)
    except (mariadb.MariaDBError, IndexError, ValueError):
        return None


def memory_floor_mb() -> int:
    """MariaDB's memory may not be capped below its buffer pool and room to work."""
    pool = buffer_pool_mb()
    return (pool or 256) + 512


def saved() -> dict:
    stored = panel_settings._read_raw().get(SETTING) or {}
    out = {key: int(stored.get(key, DEFAULTS[key])) for key in FIELDS}
    out["userstat"] = bool(stored.get("userstat", DEFAULTS["userstat"]))
    return out


def validate(values: dict) -> dict:
    clean = {}
    bounds = ranges()
    for key in FIELDS:
        low, high = bounds[key]
        try:
            number = int(values.get(key, DEFAULTS[key]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a whole number") from exc
        if not low <= number <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        clean[key] = number
    if clean["memory_max_mb"] and clean["memory_max_mb"] < memory_floor_mb():
        raise ValueError(f"MariaDB needs at least {memory_floor_mb()} MB: its buffer pool and room to work")
    clean["userstat"] = bool(values.get("userstat", True))
    return clean


def apply(values: dict) -> dict:
    clean = validate(values)
    shell.privileged("mariadb-limits-apply", helper_args=[
        str(clean["cpu_quota_percent"]), str(clean["cpu_weight"]), str(clean["io_weight"]),
        str(clean["memory_max_mb"]), str(clean["max_user_connections"]), "1" if clean["userstat"] else "0",
    ], fallback=["true"])
    # The option file is for the next start; the running server takes it now.
    mariadb._run_sql(
        f"SET GLOBAL userstat = {1 if clean['userstat'] else 0};\n"
        f"SET GLOBAL max_user_connections = {clean['max_user_connections']};\n"
    )
    data = panel_settings._read_raw()
    data[SETTING] = clean
    panel_settings._write_raw(data)
    return saved()


# --- now --------------------------------------------------------------------------------

def _read(name: str) -> str:
    try:
        return (CGROUP / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def _keyed(text: str, key: str) -> int | None:
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == key:
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def unit_usage(sample: float = SAMPLE_SECONDS) -> dict | None:
    """MariaDB's unit as the kernel has it: CPU over a short sample, memory, the limits in force."""
    before = _keyed(_read("cpu.stat"), "usage_usec")
    if before is None:
        return None
    started = time.monotonic()
    time.sleep(sample)
    after = _keyed(_read("cpu.stat"), "usage_usec") or before
    elapsed = time.monotonic() - started
    quota = _read("cpu.max").split()
    memory_max = _read("memory.max").strip()
    return {
        "cpu_percent": round((after - before) / 1e6 / elapsed * 100, 1) if elapsed > 0 else 0.0,
        "cpu_quota_percent": round(int(quota[0]) / int(quota[1]) * 100) if len(quota) == 2 and quota[0] != "max" else None,
        "memory_bytes": int(_read("memory.current").strip() or 0),
        "memory_max_bytes": int(memory_max) if memory_max.isdigit() else None,
        "throttled_usec": _keyed(_read("cpu.stat"), "throttled_usec"),
    }


def _unescape(field: str) -> str:
    """mysql's batch mode escapes \\t, \\n, \\\\ and \\0; put them back."""
    out, i = [], 0
    while i < len(field):
        ch = field[i]
        if ch == "\\" and i + 1 < len(field):
            nxt = field[i + 1]
            out.append({"t": "\t", "n": "\n", "0": "\0", "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _batch(sql: str) -> list[list[str]]:
    result = shell.run([*mariadb._mysql_args(), "-N", "-B"], check=False, input=sql, sensitive=True)
    if getattr(result, "returncode", 1) != 0:
        raise mariadb.MariaDBError((result.stderr or result.stdout or "mysql failed").strip()[:300])
    return [[_unescape(part) for part in line.split("\t")] for line in (result.stdout or "").splitlines() if line]


def server_globals() -> dict | None:
    try:
        rows = _batch("SELECT @@GLOBAL.userstat, @@GLOBAL.max_user_connections, @@GLOBAL.max_connections, "
                      "(SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME = 'THREADS_CONNECTED');")
        userstat, per_user, total, connected = rows[0]
        return {"userstat": userstat == "1", "max_user_connections": int(per_user),
                "max_connections": int(total), "threads_connected": int(connected)}
    except (mariadb.MariaDBError, IndexError, ValueError):
        return None


def _user_stats() -> dict[str, list[str]]:
    rows = _batch("SELECT USER, CONCURRENT_CONNECTIONS, TOTAL_CONNECTIONS, CPU_TIME, BUSY_TIME, ROWS_READ, ROWS_SENT "
                  "FROM information_schema.USER_STATISTICS;")
    return {row[0]: row for row in rows if len(row) == 7}


def busiest_users(owners: dict[str, str], sample: float = 2.0, limit: int = 15) -> list[dict] | None:
    """Database users by CPU over a short sample, with whose account each is."""
    try:
        first = _user_stats()
        time.sleep(sample)
        second = _user_stats()
    except mariadb.MariaDBError:
        return None
    out = []
    for user, row in second.items():
        if user in SYSTEM_USERS:
            continue
        before = first.get(user)
        try:
            cpu = float(row[3]) - (float(before[3]) if before else 0.0)
            busy = float(row[4]) - (float(before[4]) if before else 0.0)
            out.append({
                "user": user, "owner": owners.get(user),
                "connections": int(row[1]), "total_connections": int(row[2]),
                "cpu_percent": round(max(cpu, 0.0) / sample * 100, 1),
                "busy_percent": round(max(busy, 0.0) / sample * 100, 1),
                "rows_read": int(row[5]), "rows_sent": int(row[6]),
            })
        except (ValueError, IndexError):
            continue
    out.sort(key=lambda item: (item["cpu_percent"], item["busy_percent"], item["connections"]), reverse=True)
    return out[:limit]


def processes(owners: dict[str, str]) -> list[dict] | None:
    """The statements running now, MariaDB's own threads left out."""
    try:
        rows = _batch("SELECT ID, USER, IFNULL(DB, ''), COMMAND, TIME, IFNULL(STATE, ''), "
                      f"LEFT(IFNULL(INFO, ''), {INFO_CHARS}) FROM information_schema.PROCESSLIST "
                      "WHERE COMMAND NOT IN ('Sleep', 'Daemon', 'Binlog Dump') AND ID <> CONNECTION_ID() ORDER BY TIME DESC;")
    except mariadb.MariaDBError:
        return None
    out = []
    for row in rows:
        if len(row) != 7 or row[1] in SYSTEM_USERS:
            continue
        try:
            out.append({"id": int(row[0]), "user": row[1], "owner": owners.get(row[1]), "db": row[2],
                        "command": row[3], "seconds": int(row[4] or 0), "state": row[5], "info": row[6]})
        except ValueError:
            continue
    return out


def kill_query(thread_id: int) -> dict:
    """Stop one running statement (KILL QUERY): the connection stays, the statement ends."""
    rows = _batch(f"SELECT USER FROM information_schema.PROCESSLIST WHERE ID = {int(thread_id)};")
    if not rows:
        raise LookupError("That statement has already ended")
    user = rows[0][0]
    if user in SYSTEM_USERS:
        raise PermissionError("That thread is MariaDB's own")
    mariadb._run_sql(f"KILL QUERY {int(thread_id)};\n")
    return {"id": int(thread_id), "user": user}
