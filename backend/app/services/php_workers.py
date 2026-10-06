"""How many PHP workers each website's lsphp may run (operator, 2026-10-06).

Until 1.30 every vhost carried LSAPI_CHILDREN=100 and maxConns 100 -- per
site, whatever the server. On .122 (8 cores, 16 GB, 12 sites) an account with
five sites and a CPU limit of two cores grew from 13 lsphp processes to 135 as
traffic rose: with the CPU pinned at its limit every request waited longer, so
LiteSpeed started more workers, which only took memory, until the account hit
its 8 GB and the kernel killed 54 of them in twenty minutes. More workers than
the CPU can run never make a site faster; past that point a request is better
off waiting in LiteSpeed's queue than in a process holding 150 MB.

So the count is per account and follows what the account can actually run:

- WORKERS_PER_CORE workers for each core the account may use: its CPU limit
  while the Resource limits addon enforces one (a reseller's group limit
  bounds its customers too), every core of the server otherwise;
- the whole server no more than its memory holds -- RAM less MariaDB's buffer
  pool and a reserve for the system, at WORKER_MB a worker -- shared between
  accounts in proportion to the cores each may use;
- the account's number split evenly over its PHP websites, MIN_PER_SITE at
  least, so a fifth site does not multiply an account's workers by five.

The vhost template reads for_domain(). reconcile() runs on the panel's minute
tick and re-renders only the sites whose number changed, so a site added or
removed anywhere (the page, WHMCS, a restore, a DirectAdmin import), a limit
changed, or MariaDB retuned settles within a minute with one restart of
OpenLiteSpeed.
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("opanel.php_workers")

WORKERS_PER_CORE = 3
WORKER_MB = 150
MIN_PER_SITE = 2
# A site whose account the plan does not know yet (being created right now):
# a modest number until the next tick has the real one.
DEFAULT_PER_SITE = 4
PHP_APP_TYPES = {"wordpress", "php"}
MARIADB_CONF_DIR = Path("/etc/mysql/mariadb.conf.d")
CACHE_SECONDS = 10
_POOL_RE = re.compile(r"^\s*innodb_buffer_pool_size\s*=\s*(\d+)\s*([KkMmGg]?)\s*$", re.MULTILINE)
_CHILDREN_RE = re.compile(r"LSAPI_CHILDREN=(\d+)")


def ram_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return 2048


def cpu_cores() -> int:
    return os.cpu_count() or 1


def reserve_mb(total_mb: int) -> int:
    """Memory left to the system, the panel, OpenLiteSpeed and page cache."""
    return max(1024, total_mb // 8)


def _megabytes(number: str, unit: str) -> int:
    value = int(number)
    unit = unit.lower()
    if unit == "g":
        return value * 1024
    if unit == "k":
        return max(1, value // 1024)
    if unit == "m":
        return value
    return max(1, value // 1048576)  # bare bytes


def mariadb_pool_mb() -> int:
    """The buffer pool MariaDB was told to take: the last setting in the
    config directory, the order MariaDB itself reads it in."""
    pool = 0
    try:
        files = sorted(MARIADB_CONF_DIR.glob("*.cnf"))
    except OSError:
        files = []
    for path in files:
        try:
            for match in _POOL_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
                pool = _megabytes(*match.groups())
        except OSError:
            continue
    return pool or 128


def server_budget(total_mb: Optional[int] = None, pool_mb: Optional[int] = None) -> int:
    """PHP workers the whole server's memory holds."""
    total_mb = ram_mb() if total_mb is None else total_mb
    pool_mb = mariadb_pool_mb() if pool_mb is None else pool_mb
    return max(MIN_PER_SITE, (total_mb - pool_mb - reserve_mb(total_mb)) // WORKER_MB)


@dataclass
class Account:
    cores: float
    sites: list[str]


def split(accounts: dict[int, Account], budget: int, cores: int) -> dict[str, int]:
    """Workers per site, from each account's cores and sites and the server's
    memory budget. Pure, so the arithmetic is tested on its own."""
    total_cores = sum(account.cores for account in accounts.values()) or 1.0
    plan: dict[str, int] = {}
    for account in accounts.values():
        if not account.sites:
            continue
        by_cpu = max(MIN_PER_SITE, round(WORKERS_PER_CORE * account.cores))
        by_memory = int(budget * account.cores / total_cores)
        workers = max(MIN_PER_SITE, min(by_cpu, by_memory))
        per_site = max(MIN_PER_SITE, math.ceil(workers / len(account.sites)))
        for domain in account.sites:
            plan[domain] = per_site
    return plan


def _allowed_cores(user, reseller, enforcing: bool, cores: int) -> float:
    allowed = float(cores)
    if not enforcing or user is None:
        return allowed
    # Its own CPU limit; a reseller's group limit, which also bounds the
    # reseller's own account; the group limit of the reseller it belongs to.
    percents = (getattr(user, "cpu_percent", 0), getattr(user, "group_cpu_percent", 0),
                getattr(reseller, "group_cpu_percent", 0) if reseller else 0)
    for percent in percents:
        if percent and percent > 0:
            allowed = min(allowed, percent / 100)
    return max(0.25, allowed)


def build_plan(db) -> dict[str, int]:
    from app.models.entities import User, Website
    from app.services import resource_limits

    cores = cpu_cores()
    enforcing = resource_limits.enforcing()
    users = {user.id: user for user in db.query(User).all()}
    accounts: dict[int, Account] = {}
    for website in db.query(Website).order_by(Website.id).all():
        if (website.app_type or "wordpress") not in PHP_APP_TYPES or website.status == "suspended":
            continue
        user = users.get(website.owner_id)
        if website.owner_id not in accounts:
            reseller = users.get(user.reseller_id) if user is not None and user.reseller_id else None
            accounts[website.owner_id] = Account(_allowed_cores(user, reseller, enforcing, cores), [])
        accounts[website.owner_id].sites.append(website.domain)
    return split(accounts, server_budget(), cores)


_cache: dict = {"at": 0.0, "plan": None}
_cache_lock = threading.Lock()


def current_plan(fresh: bool = False) -> dict[str, int]:
    with _cache_lock:
        if not fresh and _cache["plan"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["plan"]
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        plan = build_plan(db)
    with _cache_lock:
        _cache.update(at=time.monotonic(), plan=plan)
    return plan


def for_domain(domain: str) -> int:
    """The workers a website's vhost should carry. Never raises: a vhost must
    render even when the database cannot be read."""
    try:
        return current_plan().get(domain, DEFAULT_PER_SITE)
    except Exception:  # noqa: BLE001
        logger.warning("Could not plan PHP workers for %s", domain, exc_info=True)
        return DEFAULT_PER_SITE


def configured(domain: str) -> Optional[int]:
    """What the live vhost carries now, or None when it has no lsphp (static,
    suspended, missing)."""
    from app.services import openlitespeed

    try:
        content = openlitespeed.get_vhost_config(domain)
    except Exception:  # noqa: BLE001
        return None
    match = _CHILDREN_RE.search(content or "")
    return int(match.group(1)) if match else None


_reconcile_lock = threading.Lock()


def reconcile() -> list[str]:
    """Re-render the websites whose worker count is not what the plan says,
    then restart OpenLiteSpeed once. Returns their domains."""
    from app.api.websites import _rewrite_website_vhost
    from app.core.database import SessionLocal
    from app.models.entities import Website
    from app.services import openlitespeed

    changed: list[str] = []
    with _reconcile_lock, SessionLocal() as db:
        plan = build_plan(db)
        with _cache_lock:
            _cache.update(at=time.monotonic(), plan=plan)
        for domain, workers in sorted(plan.items()):
            now = configured(domain)
            if now is None or now == workers:
                continue
            website = db.query(Website).filter(Website.domain == domain).first()
            if website is None:
                continue
            try:
                _rewrite_website_vhost(website, defer_reload=True)
                changed.append(domain)
            except Exception:  # noqa: BLE001 - one site must not stop the rest
                logger.warning("Could not re-render the vhost of %s", domain, exc_info=True)
        if changed:
            openlitespeed.reload_service()
    return changed


def reconcile_quietly() -> list[str]:
    try:
        return reconcile()
    except Exception:  # noqa: BLE001 - the minute tick must go on
        logger.warning("PHP workers were not reconciled", exc_info=True)
        return []


def summary() -> dict:
    """What the PHP tuning card shows."""
    total = ram_mb()
    pool = mariadb_pool_mb()
    return {"server_budget": server_budget(total, pool), "per_core": WORKERS_PER_CORE,
            "worker_mb": WORKER_MB, "min_per_site": MIN_PER_SITE, "ram_mb": total,
            "mariadb_pool_mb": pool, "reserve_mb": reserve_mb(total), "cores": cpu_cores()}
