"""AutoSSL: a Let's Encrypt certificate for every website whose names point here (as SNPanel's, 2026-10-17).

A pass rides on the panel's minute tick. Each website with no certificate,
or a Let's Encrypt one that misses some of its names, is checked when due:
every name is looked up on public resolvers (1.1.1.1 and 8.8.8.8), and
counts only when all its addresses are this server's - the ones found here
and any the administrator adds for NAT. When the website's own name passes,
a certificate is issued for exactly the names that passed. A failure waits
longer each time (1, 2, 4 ... hours, at most a day), so Let's Encrypt's
limits are never reached; a site with nothing to do is looked at again
daily. Manual, reused and wildcard certificates are never touched, nor a
site whose owner turned AutoSSL off.

Off until an administrator turns it on. The state of each website is kept
in autossl_sites; the settings in panel-settings.json under "autossl".
"""
from __future__ import annotations

import ipaddress
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import AutosslSite, Website
from app.services import network, panel_settings, ssl

log = logging.getLogger("opanel.autossl")

SETTING = "autossl"
RESOLVERS = ("1.1.1.1", "8.8.8.8")
RENEW_BEFORE = timedelta(days=30)
RECHECK = timedelta(days=1)
MAX_WAIT = timedelta(hours=24)
CHECKS_PER_TICK = 20
ISSUES_PER_TICK = 1
_run_lock = threading.Lock()


# --- settings ----------------------------------------------------------------------------

def settings() -> dict:
    stored = panel_settings._read_raw().get(SETTING) or {}
    return {
        "enabled": bool(stored.get("enabled", False)),
        "extra_ips": [str(item) for item in stored.get("extra_ips") or []],
        "last_run": stored.get("last_run"),
    }


def save_settings(payload: dict) -> dict:
    data = panel_settings._read_raw()
    current = dict(data.get(SETTING) or {})
    if "enabled" in payload:
        current["enabled"] = bool(payload["enabled"])
    if "extra_ips" in payload:
        raw = payload["extra_ips"]
        items = raw.replace(",", " ").split() if isinstance(raw, str) else list(raw or [])
        clean = []
        for item in items:
            try:
                clean.append(str(ipaddress.ip_address(str(item).strip())))
            except ValueError as exc:
                raise ValueError(f"Not an IP address: {item}") from exc
        if len(clean) > 16:
            raise ValueError("At most 16 extra addresses")
        current["extra_ips"] = clean
    data[SETTING] = current
    panel_settings._write_raw(data)
    return settings()


def _record_run(figures: dict) -> None:
    data = panel_settings._read_raw()
    current = dict(data.get(SETTING) or {})
    current["last_run"] = {"at": datetime.utcnow().isoformat() + "Z", **figures}
    data[SETTING] = current
    panel_settings._write_raw(data)


# --- the checks ------------------------------------------------------------------------------

def server_ips() -> set[str]:
    try:
        found = network.detect_addresses()
    except Exception:  # noqa: BLE001
        found = {"ipv4": [], "ipv6": []}
    ips = {str(ipaddress.ip_address(item)) for item in (found.get("ipv4") or []) + (found.get("ipv6") or [])}
    return ips | set(settings()["extra_ips"])


def _resolve(name: str) -> tuple[list[str], str]:
    """The name's addresses on the public resolvers, and the error when none answered."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return [], "the DNS library is missing"
    found: set[str] = set()
    error = ""
    for server in RESOLVERS:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [server]
        resolver.timeout = 3
        resolver.lifetime = 5
        for rtype in ("A", "AAAA"):
            try:
                found.update(str(item) for item in resolver.resolve(name, rtype))
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                continue
            except (dns.exception.DNSException, OSError) as exc:
                error = f"{server}: {exc.__class__.__name__}"
    return sorted(found), error


def check_name(name: str, ours: set[str]) -> dict:
    addresses, error = _resolve(name)
    if not addresses:
        return {"name": name, "addresses": [], "ok": False,
                "reason": error or "The name does not resolve on public DNS"}
    elsewhere = [address for address in addresses if address not in ours]
    if elsewhere:
        return {"name": name, "addresses": addresses, "ok": False,
                "reason": f"It points at {', '.join(elsewhere)}, not at this server"}
    return {"name": name, "addresses": addresses, "ok": True, "reason": ""}


def wanted_names(website: Website) -> list[str]:
    names = [website.domain]
    for alias in sorted(getattr(website, "aliases", []) or [], key=lambda item: item.domain):
        if alias.domain and alias.domain not in names:
            names.append(alias.domain)
    return names


def current_certificate(website: Website) -> dict | None:
    """The Let's Encrypt certificate the panel can read for the site: names and expiry."""
    pem = ssl._read_cert_bytes(ssl.PANEL_CERT_STORE / website.domain / "fullchain.pem")
    if not pem:
        return None
    names, expiry = ssl._cert_names_and_expiry(pem)
    if expiry is not None and expiry.tzinfo is not None:
        expiry = expiry.replace(tzinfo=None)
    return {"names": names, "expires_at": expiry.isoformat() + "Z" if expiry else None, "_expiry": expiry}


def _state(db: Session, website: Website) -> AutosslSite:
    row = db.get(AutosslSite, website.id)
    if row is None:
        row = AutosslSite(website_id=website.id, enabled=True, failures=0, names="[]")
        db.add(row)
        db.flush()
    return row


def needs_work(website: Website) -> tuple[bool, str]:
    """Whether AutoSSL has something to do for the site, and why not when it has not."""
    mode = getattr(website, "ssl_mode", "none") or "none"
    if mode in {"manual", "reuse"} or getattr(website, "ssl_wildcard", False):
        return False, "The website has a certificate of its own choosing"
    if mode != "letsencrypt" or not website.ssl_enabled:
        return True, "No certificate yet"
    cert = current_certificate(website)
    if cert is None:
        return True, "The certificate could not be read"
    missing = [name for name in wanted_names(website) if name not in cert["names"]]
    if missing:
        return True, f"The certificate misses {', '.join(missing)}"
    expiry = cert["_expiry"]
    if expiry and expiry - datetime.utcnow() < RENEW_BEFORE:
        return True, "The certificate expires within 30 days"
    return False, "Secured"


def run_site(db: Session, website: Website, *, force: bool = False) -> dict:
    """Check one website, and issue when its own name points here. Commits."""
    row = _state(db, website)
    now = datetime.utcnow()
    if not row.enabled and not force:
        db.commit()
        return serialize(website, row)
    todo, why = needs_work(website)
    if not todo:
        row.names = json.dumps([])
        row.last_check, row.next_check, row.failures, row.last_error = now, now + RECHECK, 0, ""
        row.status = "secured" if why == "Secured" else "skipped"
        db.commit()
        return serialize(website, row)
    ours = server_ips()
    results = [check_name(name, ours) for name in wanted_names(website)]
    row.names = json.dumps(results)
    row.last_check = now
    passing = [item["name"] for item in results if item["ok"]]
    if website.domain not in passing:
        row.status = "waiting"
        row.last_error = next((item["reason"] for item in results if item["name"] == website.domain), "")
        _back_off(row, now)
        db.commit()
        return serialize(website, row)
    from app.api.websites import LetsEncryptError, issue_letsencrypt

    try:
        issue_letsencrypt(db, website, passing)
    except LetsEncryptError as exc:
        db.rollback()
        row = _state(db, website)
        row.names = json.dumps(results)
        row.last_check = now
        row.status = "failed"
        row.last_error = str(exc)[:2000]
        _back_off(row, now)
        db.commit()
        log.warning("AutoSSL could not secure %s: %s", website.domain, exc)
        return serialize(website, row)
    row = _state(db, website)
    row.status, row.failures, row.last_error = "secured", 0, ""
    row.last_issued_at, row.next_check = now, now + RECHECK
    db.commit()
    return {**serialize(website, row), "issued": True}


def _back_off(row: AutosslSite, now: datetime) -> None:
    row.failures = (row.failures or 0) + 1
    row.next_check = now + min(timedelta(hours=2 ** (row.failures - 1)), MAX_WAIT)


def tick(db: Session | None = None, *, force: bool = False) -> dict:
    """One pass: the websites due, a few checks and at most one certificate."""
    if not settings()["enabled"] and not force:
        return {"checked": 0, "issued": 0, "failed": 0}
    if not _run_lock.acquire(blocking=False):
        return {"checked": 0, "issued": 0, "failed": 0, "busy": True}
    own = db is None
    if own:
        from app.core.database import SessionLocal
        db = SessionLocal()
    figures = {"checked": 0, "issued": 0, "failed": 0}
    try:
        now = datetime.utcnow()
        states = {row.website_id: row for row in db.query(AutosslSite).all()}
        due = []
        for website in db.query(Website).order_by(Website.id).all():
            row = states.get(website.id)
            if row is not None and not row.enabled:
                continue
            if (website.status or "active") != "active":
                continue
            if force or row is None or row.next_check is None or row.next_check <= now:
                due.append(website)
        for website in due[:CHECKS_PER_TICK]:
            if figures["issued"] + figures["failed"] >= ISSUES_PER_TICK:
                break
            result = run_site(db, website)
            figures["checked"] += 1
            if result.get("issued"):
                figures["issued"] += 1
            elif result["status"] == "failed":
                figures["failed"] += 1
        _record_run(figures)
        return figures
    finally:
        if own:
            db.close()
        _run_lock.release()


def reset_site(db: Session, website_id: int) -> None:
    """A website changed (made, an alias added): look at it on the next pass.

    Best effort: the change it follows must never fail on it.
    """
    try:
        row = db.get(AutosslSite, website_id)
    except Exception:  # noqa: BLE001
        return
    if row is not None:
        row.next_check = None
        row.failures = 0


def serialize(website: Website, row: Optional[AutosslSite]) -> dict:
    cert = current_certificate(website) if (website.ssl_mode or "") == "letsencrypt" else None
    todo, why = needs_work(website)
    return {
        "website_id": website.id, "domain": website.domain,
        "enabled": row.enabled if row is not None else True,
        "status": (row.status if row is not None and row.status else ("pending" if todo else "secured")),
        "why": why,
        "names": json.loads(row.names or "[]") if row is not None else [],
        "failures": row.failures if row is not None else 0,
        "last_error": row.last_error if row is not None else "",
        "last_check": row.last_check.isoformat() + "Z" if row is not None and row.last_check else None,
        "next_check": row.next_check.isoformat() + "Z" if row is not None and row.next_check else None,
        "last_issued_at": row.last_issued_at.isoformat() + "Z" if row is not None and row.last_issued_at else "",
        "certificate": {key: value for key, value in cert.items() if not key.startswith("_")} if cert else None,
        "ssl_mode": website.ssl_mode or "none",
    }
