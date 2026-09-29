"""Email addon: mail domains, mailboxes and forwarders.

The panel's database is the source of truth. After every change the whole
state goes to the root helper (mail-sync), which renders the maps Exim and
Dovecot read on every lookup. There is no partial update: a sync that fails
leaves the previous maps in force, and the next successful one repairs
everything.

Who may do what:
- An end user manages mail for the domains of their own websites (and aliases)
  and nothing else; an administrator manages every domain.
- Mailboxes count against the owner's mailbox_limit (0 = unlimited); an
  administrator acting for a customer is not held to it.
- Webmail opens with a signed, single-use link (SSO) that the webmail redeems
  as a Dovecot master user, so no mailbox password is ever stored or sent.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import socket
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import bcrypt
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core import secrets as secret_store
from app.core.config import settings
from app.core.permissions import is_admin_role
from app.models.entities import MailDomain, Mailbox, MailForwarder, User, Website, WebsiteAlias
from app.services import addons, network, panel_settings
from app.services.shell import shell

ADDON_ID = "mail"
MARKER = Path("/etc/opanel-mail/installed")
SSO_SECRET_FILE = Path("/var/lib/opanel/addons/mail-sso.secret")
DKIM_SELECTOR = "opanel"
WEBMAIL_PORT = 2096
# The webmail refuses a link that lives longer than 300 s.
SSO_TTL_SECONDS = 120
MAX_DESTINATIONS = 20
DEFAULT_QUOTA_MB = 1024
# An end user may not create an unlimited mailbox.
MAX_USER_QUOTA_MB = 51200
MAX_QUOTA_MB = 1048576
USAGE_CACHE_SECONDS = 120

# Kept in step with the helper's checks (mail_sync_from_stdin).
LOCAL_PART_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9_+-])?$")
DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
DESTINATION_RE = re.compile(
    r"^[A-Za-z0-9._%+=-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$|^(?:\d{1,3}\.){3}\d{1,3}$")

SETTINGS_DEFAULTS = {
    "smarthost_enabled": False,
    "smarthost_host": "",
    "smarthost_port": 587,
    "smarthost_username": "",
    "auth_rate_per_hour": 300,
    "local_rate_per_hour": 300,
    "max_message_mb": 50,
    "spam_header_score": 6.0,
    "spam_reject_score": 15.0,
    "greylisting": True,
    "default_quota_mb": DEFAULT_QUOTA_MB,
}

_sync_lock = threading.Lock()
_usage_cache: dict = {"at": 0.0, "data": {}}


class NotInstalled(RuntimeError):
    """The Email addon is not installed on this server."""


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------
def installed() -> bool:
    try:
        return MARKER.exists()
    except OSError:
        return False


def require_installed() -> None:
    if not installed():
        raise NotInstalled("The Email addon is not installed. An administrator can install it from Settings › Addons.")


def hostname() -> str:
    """The mail server's name: MX target, IMAP/SMTP host and webmail host.

    The helper derives it the same way (mail_hostname), so what the panel
    tells customers to use is what Exim and the certificate answer to.
    """
    host = (settings.panel_domain or "").strip().lower()
    if not DOMAIN_RE.fullmatch(host):
        url = panel_settings.current_settings().get("panel_url") or settings.panel_url or ""
        parsed = urlparse(url if "://" in url else f"//{url}")
        host = (parsed.hostname or "").lower()
    if not DOMAIN_RE.fullmatch(host):
        host = socket.getfqdn().lower()
    return host if DOMAIN_RE.fullmatch(host) else ""


def server_ipv4() -> str:
    try:
        addresses = network.detect_addresses().get("ipv4") or []
    except Exception:  # noqa: BLE001 - informational only
        addresses = []
    return addresses[0] if addresses else ""


def webmail_base(mail_domain: Optional[MailDomain] = None) -> str:
    if mail_domain is not None and mail_domain.webmail_host:
        return f"https://webmail.{mail_domain.domain}"
    return f"https://{hostname() or server_ipv4()}:{WEBMAIL_PORT}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def normalize_domain(value: str) -> str:
    name = (value or "").strip().lower().rstrip(".")
    if not DOMAIN_RE.fullmatch(name) or len(name) > 253:
        raise ValueError("Enter a domain name such as example.com")
    return name


def normalize_local_part(value: str) -> str:
    local = (value or "").strip().lower()
    if not LOCAL_PART_RE.fullmatch(local) or ".." in local:
        raise ValueError("A mailbox name may use letters, digits and . _ + -, "
                         "and must start with a letter or digit")
    return local


def normalize_destination(value: str) -> str:
    address = (value or "").strip()
    if not DESTINATION_RE.fullmatch(address) or ".." in address or len(address) > 254:
        raise ValueError(f"Not an email address: {value}")
    return address.lower()


def normalize_destinations(values, own_address: str = "") -> list[str]:
    clean: list[str] = []
    for raw in values or []:
        for part in re.split(r"[\s,;]+", str(raw or "")):
            if not part:
                continue
            address = normalize_destination(part)
            if address == own_address:
                raise ValueError("A forwarder cannot forward to itself; add a mailbox with the same name instead")
            if address not in clean:
                clean.append(address)
    if not clean:
        raise ValueError("Enter at least one destination address")
    if len(clean) > MAX_DESTINATIONS:
        raise ValueError(f"At most {MAX_DESTINATIONS} destinations")
    return clean


def check_password(password: str, address: str) -> None:
    value = password or ""
    if len(value) < 8:
        raise ValueError("The password must be at least 8 characters")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("The password must be at most 72 bytes")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("The password must contain both letters and digits")
    if address.split("@", 1)[0] in value.lower() and len(address.split("@", 1)[0]) >= 3:
        raise ValueError("The password must not contain the mailbox name")


def hash_password(password: str) -> str:
    """A Dovecot BLF-CRYPT hash. $2y$ is what Dovecot itself writes; the
    algorithm is the same as $2b$."""
    digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")
    if digest.startswith("$2b$"):
        digest = "$2y$" + digest[4:]
    return "{BLF-CRYPT}" + digest


def _quota(actor: User, value: Optional[int]) -> int:
    if value is None:
        value = int(current_settings().get("default_quota_mb") or DEFAULT_QUOTA_MB)
    try:
        quota = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("The quota must be a whole number of MB") from exc
    if is_admin_role(actor.role):
        if quota < 0 or quota > MAX_QUOTA_MB:
            raise ValueError(f"The quota must be between 0 (unlimited) and {MAX_QUOTA_MB} MB")
        return quota
    if quota < 1 or quota > MAX_USER_QUOTA_MB:
        raise ValueError(f"The quota must be between 1 and {MAX_USER_QUOTA_MB} MB")
    return quota


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def _is_admin(user: User) -> bool:
    return is_admin_role(user.role)


def get_domain(db: Session, actor: User, domain_id: int) -> MailDomain:
    row = db.get(MailDomain, domain_id)
    if row is None or (not _is_admin(actor) and row.owner_id != actor.id):
        raise LookupError("Mail domain not found")
    return row


def get_mailbox(db: Session, actor: User, mailbox_id: int) -> Mailbox:
    row = db.get(Mailbox, mailbox_id)
    if row is None or (not _is_admin(actor) and row.mail_domain.owner_id != actor.id):
        raise LookupError("Mailbox not found")
    return row


def get_forwarder(db: Session, actor: User, forwarder_id: int) -> MailForwarder:
    row = db.get(MailForwarder, forwarder_id)
    if row is None or (not _is_admin(actor) and row.mail_domain.owner_id != actor.id):
        raise LookupError("Forwarder not found")
    return row


def _website_owner(db: Session, name: str) -> Optional[User]:
    site = db.query(Website).filter(func.lower(Website.domain) == name).first()
    if site is not None:
        return site.owner
    alias = db.query(WebsiteAlias).filter(func.lower(WebsiteAlias.domain) == name).first()
    if alias is not None and alias.website is not None:
        return alias.website.owner
    return None


def candidate_domains(db: Session, actor: User) -> list[str]:
    """Website and alias names mail could be turned on for."""
    sites = db.query(Website)
    if not _is_admin(actor):
        sites = sites.filter(Website.owner_id == actor.id)
    names: set[str] = set()
    for site in sites.all():
        names.add((site.domain or "").lower())
        for alias in site.aliases or []:
            names.add((alias.domain or "").lower())
    taken = {row[0] for row in db.query(MailDomain.domain).all()}
    return sorted(n for n in names if DOMAIN_RE.fullmatch(n) and not n.startswith("www.") and n not in taken)


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------
def _dkim_public(domain: str, rotate: bool = False) -> str:
    args = [domain, "rotate"] if rotate else [domain]
    result = shell.privileged("mail-dkim", helper_args=args, check=False)
    key = (result.stdout or "").strip().splitlines()[-1:] if result.returncode == 0 else []
    if not key or not re.fullmatch(r"[A-Za-z0-9+/=]{200,1000}", key[0]):
        raise RuntimeError((result.stderr or result.stdout or "Could not create the DKIM key").strip())
    return key[0]


def add_domain(db: Session, actor: User, domain: str, owner_id: Optional[int] = None) -> MailDomain:
    require_installed()
    name = normalize_domain(domain)
    if db.query(MailDomain).filter(MailDomain.domain == name).first() is not None:
        raise ValueError(f"Email is already on for {name}")
    owner = _website_owner(db, name)
    if _is_admin(actor):
        if owner_id:
            owner = db.get(User, owner_id)
            if owner is None:
                raise ValueError("That account does not exist")
        elif owner is None:
            owner = actor
    elif owner is None or owner.id != actor.id:
        raise PermissionError("You can only turn on email for the domains of your own websites")
    row = MailDomain(domain=name, owner_id=owner.id, catch_all="", webmail_host=False,
                     dkim_public=_dkim_public(name))
    db.add(row)
    db.commit()
    db.refresh(row)
    sync(db)
    return row


def delete_domain(db: Session, actor: User, domain_id: int) -> str:
    row = get_domain(db, actor, domain_id)
    name, webmail = row.domain, bool(row.webmail_host)
    db.delete(row)
    db.commit()
    sync(db)
    if webmail:
        shell.privileged("mail-webmail-host", helper_args=[name, "off"], check=False)
    # Last, once nothing routes to it any more.
    shell.privileged("mail-purge-domain", helper_args=[name], check=False)
    return name


def delete_for_owner(db: Session, owner: User) -> list[str]:
    """An account is being deleted: its mail goes with it. The caller commits."""
    rows = db.query(MailDomain).filter(MailDomain.owner_id == owner.id).all()
    names = [row.domain for row in rows]
    if not rows:
        return names
    webmail = [row.domain for row in rows if row.webmail_host]
    for row in rows:
        db.delete(row)
    db.flush()
    if installed():
        sync(db)
        for name in webmail:
            shell.privileged("mail-webmail-host", helper_args=[name, "off"], check=False)
    for name in names:
        shell.privileged("mail-purge-domain", helper_args=[name], check=False)
    return names


def set_catch_all(db: Session, actor: User, domain_id: int, target: str) -> MailDomain:
    row = get_domain(db, actor, domain_id)
    value = (target or "").strip()
    if value:
        value = normalize_destination(value)
        local, _, domain = value.partition("@")
        if domain == row.domain:
            # Pointing at an address of this domain that does not exist would
            # hand the message straight back to the catch-all.
            exists = any(m.local_part == local for m in row.mailboxes) or \
                any(f.local_part == local for f in row.forwarders)
            if not exists:
                raise ValueError(f"{value} is not a mailbox or forwarder of {row.domain}")
    row.catch_all = value
    db.commit()
    sync(db)
    return row


def rotate_dkim(db: Session, actor: User, domain_id: int) -> MailDomain:
    require_installed()
    row = get_domain(db, actor, domain_id)
    row.dkim_public = _dkim_public(row.domain, rotate=True)
    db.commit()
    sync(db)
    return row


def set_webmail_host(db: Session, actor: User, domain_id: int, enabled: bool) -> MailDomain:
    require_installed()
    row = get_domain(db, actor, domain_id)
    host = f"webmail.{row.domain}"
    if enabled:
        taken = db.query(Website).filter(func.lower(Website.domain) == host).first() is not None or \
            db.query(WebsiteAlias).filter(func.lower(WebsiteAlias.domain) == host).first() is not None
        if taken:
            raise ValueError(f"{host} is already a website on this server")
        args = [row.domain, "on"]
        if settings.ssl_email:
            args.append(settings.ssl_email)
        result = shell.privileged("mail-webmail-host", helper_args=args, check=False)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or f"Could not set up {host}").strip()
                               .replace("opanel-helper: ", ""))
    else:
        shell.privileged("mail-webmail-host", helper_args=[row.domain, "off"], check=False)
    row.webmail_host = bool(enabled)
    db.commit()
    return row


def webmail_host_taken(db: Session, domain: str) -> bool:
    """Whether a website name is the webmail.<domain> host of a mail domain."""
    name = (domain or "").strip().lower()
    if not name.startswith("webmail."):
        return False
    return db.query(MailDomain).filter(MailDomain.domain == name[len("webmail."):],
                                       MailDomain.webmail_host.is_(True)).first() is not None


# ---------------------------------------------------------------------------
# Mailboxes
# ---------------------------------------------------------------------------
def owner_mailbox_count(db: Session, owner_id: int) -> int:
    return db.query(func.count(Mailbox.id)).join(MailDomain).filter(MailDomain.owner_id == owner_id).scalar() or 0


def create_mailbox(db: Session, actor: User, domain_id: int, local_part: str, password: str,
                   quota_mb: Optional[int] = None) -> Mailbox:
    require_installed()
    domain = get_domain(db, actor, domain_id)
    local = normalize_local_part(local_part)
    address = f"{local}@{domain.domain}"
    if any(m.local_part == local for m in domain.mailboxes):
        raise ValueError(f"{address} already exists")
    owner = domain.owner
    limit = int(owner.mailbox_limit or 0)
    if not _is_admin(actor) and not is_admin_role(owner.role) and limit and \
            owner_mailbox_count(db, owner.id) >= limit:
        raise ValueError(f"Mailbox limit reached ({limit}). Ask your provider for more.")
    check_password(password, address)
    row = Mailbox(domain_id=domain.id, local_part=local, password_hash=hash_password(password),
                  quota_mb=_quota(actor, quota_mb), enabled=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    sync(db)
    return row


def update_mailbox(db: Session, actor: User, mailbox_id: int, *, password: Optional[str] = None,
                   quota_mb: Optional[int] = None, enabled: Optional[bool] = None) -> Mailbox:
    row = get_mailbox(db, actor, mailbox_id)
    if password is not None:
        check_password(password, row.address)
        row.password_hash = hash_password(password)
    if quota_mb is not None:
        row.quota_mb = _quota(actor, quota_mb)
    if enabled is not None:
        row.enabled = bool(enabled)
    db.commit()
    sync(db)
    return row


def delete_mailbox(db: Session, actor: User, mailbox_id: int) -> str:
    row = get_mailbox(db, actor, mailbox_id)
    address = row.address
    domain = row.mail_domain
    if domain.catch_all == address and not any(f.local_part == row.local_part for f in domain.forwarders):
        domain.catch_all = ""
    db.delete(row)
    db.commit()
    sync(db)
    shell.privileged("mail-purge-mailbox", helper_args=[address], check=False)
    return address


def list_mailboxes(db: Session, actor: User, domain_id: Optional[int] = None, q: str = "",
                   page: int = 1, per_page: int = 50) -> dict:
    query = db.query(Mailbox).join(MailDomain)
    if not _is_admin(actor):
        query = query.filter(MailDomain.owner_id == actor.id)
    if domain_id:
        query = query.filter(Mailbox.domain_id == domain_id)
    term = (q or "").strip().lower()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(Mailbox.local_part.like(like), MailDomain.domain.like(like)))
    total = query.count()
    page, per_page = max(1, int(page or 1)), max(1, min(int(per_page or 50), 200))
    rows = query.order_by(MailDomain.domain, Mailbox.local_part).offset((page - 1) * per_page).limit(per_page).all()
    usage = usage_kib()
    return {"items": [mailbox_out(row, usage) for row in rows], "total": total, "page": page, "per_page": per_page}


def mailbox_out(row: Mailbox, usage: Optional[dict] = None) -> dict:
    kib = (usage or {}).get(row.address)
    return {
        "id": row.id,
        "address": row.address,
        "local_part": row.local_part,
        "domain_id": row.domain_id,
        "domain": row.mail_domain.domain,
        "owner": row.mail_domain.owner.username if row.mail_domain.owner else "",
        "quota_mb": row.quota_mb or 0,
        "used_mb": round(kib / 1024, 1) if kib is not None else None,
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# Forwarders
# ---------------------------------------------------------------------------
def create_forwarder(db: Session, actor: User, domain_id: int, local_part: str, destinations) -> MailForwarder:
    require_installed()
    domain = get_domain(db, actor, domain_id)
    local = normalize_local_part(local_part)
    address = f"{local}@{domain.domain}"
    if any(f.local_part == local for f in domain.forwarders):
        raise ValueError(f"{address} already forwards; edit that forwarder instead")
    targets = normalize_destinations(destinations, own_address=address)
    row = MailForwarder(domain_id=domain.id, local_part=local, destinations="\n".join(targets))
    db.add(row)
    db.commit()
    db.refresh(row)
    sync(db)
    return row


def update_forwarder(db: Session, actor: User, forwarder_id: int, destinations) -> MailForwarder:
    row = get_forwarder(db, actor, forwarder_id)
    row.destinations = "\n".join(normalize_destinations(destinations, own_address=row.address))
    db.commit()
    sync(db)
    return row


def delete_forwarder(db: Session, actor: User, forwarder_id: int) -> str:
    row = get_forwarder(db, actor, forwarder_id)
    address = row.address
    domain = row.mail_domain
    if domain.catch_all == address and not any(m.local_part == row.local_part for m in domain.mailboxes):
        domain.catch_all = ""
    db.delete(row)
    db.commit()
    sync(db)
    return address


def list_forwarders(db: Session, actor: User, domain_id: Optional[int] = None, q: str = "",
                    page: int = 1, per_page: int = 50) -> dict:
    query = db.query(MailForwarder).join(MailDomain)
    if not _is_admin(actor):
        query = query.filter(MailDomain.owner_id == actor.id)
    if domain_id:
        query = query.filter(MailForwarder.domain_id == domain_id)
    term = (q or "").strip().lower()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(MailForwarder.local_part.like(like), MailDomain.domain.like(like),
                                 MailForwarder.destinations.like(like)))
    total = query.count()
    page, per_page = max(1, int(page or 1)), max(1, min(int(per_page or 50), 200))
    rows = query.order_by(MailDomain.domain, MailForwarder.local_part) \
        .offset((page - 1) * per_page).limit(per_page).all()
    return {"items": [forwarder_out(row) for row in rows], "total": total, "page": page, "per_page": per_page}


def forwarder_out(row: MailForwarder) -> dict:
    return {
        "id": row.id,
        "address": row.address,
        "local_part": row.local_part,
        "domain_id": row.domain_id,
        "domain": row.mail_domain.domain,
        "destinations": row.destination_list,
        "keeps_copy": any(m.local_part == row.local_part for m in row.mail_domain.mailboxes),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def domain_out(row: MailDomain) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "owner_id": row.owner_id,
        "owner": row.owner.username if row.owner else "",
        "catch_all": row.catch_all or "",
        "webmail_host": bool(row.webmail_host),
        "webmail_url": webmail_base(row) + "/",
        "mailboxes": len(row.mailboxes),
        "forwarders": len(row.forwarders),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def overview(db: Session, actor: User) -> dict:
    is_installed = installed()
    domains = db.query(MailDomain)
    if not _is_admin(actor):
        domains = domains.filter(MailDomain.owner_id == actor.id)
    host = hostname()
    return {
        "installed": is_installed,
        "is_admin": _is_admin(actor),
        "hostname": host,
        "webmail_url": webmail_base() + "/",
        "mailbox_limit": int(actor.mailbox_limit or 0),
        "mailbox_count": owner_mailbox_count(db, actor.id),
        "max_user_quota_mb": MAX_USER_QUOTA_MB,
        "default_quota_mb": int(current_settings().get("default_quota_mb") or DEFAULT_QUOTA_MB),
        "domains": [domain_out(row) for row in domains.order_by(MailDomain.domain).all()],
        "candidates": candidate_domains(db, actor) if is_installed else [],
        "client": {
            "host": host,
            "imap_port": 993,
            "pop3_port": 995,
            "smtp_port": 465,
            "submission_port": 587,
        },
    }


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------
def dns_records(row: MailDomain) -> list[dict]:
    host = hostname()
    try:
        addresses = network.detect_addresses()
    except Exception:  # noqa: BLE001
        addresses = {"ipv4": [], "ipv6": []}
    ipv4, ipv6 = addresses.get("ipv4") or [], addresses.get("ipv6") or []
    spf = ["v=spf1", "mx", "a"] + [f"ip4:{ip}" for ip in ipv4[:2]] + [f"ip6:{ip}" for ip in ipv6[:1]]
    relay = current_settings()
    note = ""
    if relay.get("smarthost_enabled") and relay.get("smarthost_host"):
        note = "Outgoing mail leaves through your relay: add the SPF include your relay provider documents."
    records = [
        {"key": "mx", "type": "MX", "name": row.domain, "value": host, "priority": 10},
        {"key": "spf", "type": "TXT", "name": row.domain, "value": " ".join(spf + ["~all"]), "note": note},
        {"key": "dkim", "type": "TXT", "name": f"{DKIM_SELECTOR}._domainkey.{row.domain}",
         "value": f"v=DKIM1; k=rsa; p={row.dkim_public}"},
        {"key": "dmarc", "type": "TXT", "name": f"_dmarc.{row.domain}",
         "value": "v=DMARC1; p=quarantine; adkim=r; aspf=r"},
    ]
    if ipv4:
        records.append({"key": "webmail", "type": "A", "name": f"webmail.{row.domain}", "value": ipv4[0],
                        "optional": True})
    return records


def _resolve(name: str, rtype: str) -> Optional[list[str]]:
    """Answers as text, [] for no record, None when DNS could not be asked."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return None
    resolver = dns.resolver.Resolver()
    resolver.timeout = 3
    resolver.lifetime = 4
    try:
        answer = resolver.resolve(name, rtype)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.exception.DNSException, OSError):
        return None
    out = []
    for item in answer:
        if rtype == "TXT":
            out.append(b"".join(item.strings).decode("utf-8", "replace"))
        elif rtype == "MX":
            out.append(str(item.exchange).rstrip(".").lower())
        else:
            out.append(item.to_text())
    return out


def check_dns(row: MailDomain) -> list[dict]:
    records = dns_records(row)
    host = hostname()
    ipv4 = [r["value"] for r in records if r["key"] == "webmail"]
    for record in records:
        key = record["key"]
        if key == "mx":
            found = _resolve(row.domain, "MX")
            ok = found is not None and host in found
        elif key == "spf":
            txt = _resolve(row.domain, "TXT")
            found = None if txt is None else [t for t in txt if t.lower().startswith("v=spf1")]
            ok = bool(found) and any(" mx" in t or any(ip in t for ip in ipv4) for t in found)
        elif key == "dkim":
            found = _resolve(record["name"], "TXT")
            ok = found is not None and any(row.dkim_public and row.dkim_public in t.replace(" ", "") for t in found)
        elif key == "dmarc":
            found = _resolve(record["name"], "TXT")
            ok = found is not None and any(t.upper().startswith("V=DMARC1") for t in found)
        else:
            found = _resolve(record["name"], "A")
            ok = found is not None and record["value"] in found
        if found is None:
            record["status"] = "unknown"
        elif ok:
            record["status"] = "ok"
        else:
            record["status"] = "different" if found else "missing"
        record["found"] = found or []
    return records


# ---------------------------------------------------------------------------
# Webmail single sign-on
# ---------------------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sso_url(db: Session, actor: User, mailbox_id: int) -> str:
    require_installed()
    row = get_mailbox(db, actor, mailbox_id)
    if not row.enabled:
        raise ValueError("This mailbox is suspended")
    try:
        secret = SSO_SECRET_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("Webmail sign-on is not set up on this server; reinstall the Email addon") from exc
    if len(secret) < 32:
        raise RuntimeError("Webmail sign-on is not set up on this server; reinstall the Email addon")
    claims = {"email": row.address, "exp": int(time.time()) + SSO_TTL_SECONDS, "nonce": secrets.token_urlsafe(24)}
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signature = _b64(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{webmail_base(row.mail_domain)}/sso?token={payload}.{signature}"


# ---------------------------------------------------------------------------
# Sync with the box
# ---------------------------------------------------------------------------
def build_state(db: Session) -> dict:
    domains = db.query(MailDomain).order_by(MailDomain.domain).all()
    by_owner: dict[int, list[str]] = defaultdict(list)
    for row in domains:
        by_owner[row.owner_id].append(row.domain)
    state: dict = {
        "domains": [{"domain": row.domain, "catch_all": row.catch_all or ""} for row in domains],
        "mailboxes": [],
        "forwarders": [],
        # A mailbox may send as any domain of the account that owns it.
        "senders": {},
        # PHP on an account's websites may have its mail signed for that
        # account's domains.
        "local_senders": {},
    }
    for row in domains:
        for box in row.mailboxes:
            address = f"{box.local_part}@{row.domain}"
            state["mailboxes"].append({"address": address, "hash": box.password_hash,
                                       "quota_mb": int(box.quota_mb or 0), "enabled": bool(box.enabled)})
            state["senders"][address] = by_owner[row.owner_id]
        for forward in row.forwarders:
            state["forwarders"].append({"address": f"{forward.local_part}@{row.domain}",
                                        "to": forward.destination_list})
    if by_owner:
        for site in db.query(Website).filter(Website.owner_id.in_(list(by_owner))).all():
            if site.linux_user:
                merged = state["local_senders"].setdefault(site.linux_user, [])
                for name in by_owner[site.owner_id]:
                    if name not in merged:
                        merged.append(name)
    return state


def sync(db: Session) -> None:
    if not installed():
        return
    state = build_state(db)
    with _sync_lock:
        result = shell.privileged("mail-sync", input=json.dumps(state), check=False, sensitive=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "mail-sync failed").strip().replace("opanel-helper: ", "")
        raise RuntimeError(f"Saved, but the mail server could not be updated: {detail}")
    # A sync that works again clears the error a failed one left on the addon card.
    if "mailboxes could not be written out" in (addons._entry(ADDON_ID).get("last_error") or ""):
        addons._update_state(ADDON_ID, last_error="")


def usage_kib() -> dict[str, int]:
    now = time.monotonic()
    if now - _usage_cache["at"] < USAGE_CACHE_SECONDS:
        return _usage_cache["data"]
    data: dict[str, int] = {}
    if installed():
        result = shell.privileged("mail-usage", check=False)
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                address, _, size = line.strip().partition(" ")
                if address and size.isdigit():
                    data[address] = int(size)
    _usage_cache.update(at=now, data=data)
    return data


def after_lifecycle(action: str) -> None:
    """Called once an install or removal has finished."""
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        if action == "install":
            apply_settings()
            # Keys kept from an earlier install are reused; any that went
            # missing are made again, and the record shown follows.
            for row in db.query(MailDomain).all():
                try:
                    row.dkim_public = _dkim_public(row.domain)
                except RuntimeError:
                    continue
            db.commit()
            sync(db)
        elif action == "uninstall":
            # Removal takes every webmail.<domain> host down with it.
            for row in db.query(MailDomain).filter(MailDomain.webmail_host.is_(True)).all():
                row.webmail_host = False
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Server settings (administrators)
# ---------------------------------------------------------------------------
def _stored_settings() -> dict:
    stored = addons._entry(ADDON_ID).get("settings")
    return dict(stored) if isinstance(stored, dict) else {}


def current_settings() -> dict:
    """The settings as the page shows them: no password, only whether one is set."""
    stored = _stored_settings()
    merged = dict(SETTINGS_DEFAULTS)
    for key in SETTINGS_DEFAULTS:
        if key in stored:
            merged[key] = stored[key]
    merged["smarthost_password_set"] = bool(stored.get("smarthost_password"))
    return merged


def save_settings(payload: dict) -> dict:
    stored = _stored_settings()
    merged = {key: stored.get(key, default) for key, default in SETTINGS_DEFAULTS.items()}
    if stored.get("smarthost_password"):
        merged["smarthost_password"] = stored["smarthost_password"]

    def whole(key, low, high, label):
        try:
            value = int(payload[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a whole number") from exc
        if not low <= value <= high:
            raise ValueError(f"{label} must be between {low} and {high}")
        return value

    def score(key, label):
        try:
            value = float(payload[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a number") from exc
        if not 1 <= value <= 100:
            raise ValueError(f"{label} must be between 1 and 100")
        return value

    if "smarthost_enabled" in payload:
        merged["smarthost_enabled"] = bool(payload["smarthost_enabled"])
    if "smarthost_host" in payload:
        host = str(payload["smarthost_host"] or "").strip().lower()
        if host and not HOST_RE.fullmatch(host):
            raise ValueError("The relay host must be a hostname such as smtp.example.com")
        merged["smarthost_host"] = host
    if "smarthost_port" in payload:
        merged["smarthost_port"] = whole("smarthost_port", 1, 65535, "The relay port")
    if "smarthost_username" in payload:
        username = str(payload["smarthost_username"] or "").strip()
        if len(username) > 255 or any(ch in username for ch in "\r\n\0"):
            raise ValueError("The relay username is not valid")
        merged["smarthost_username"] = username
        if not username:
            merged.pop("smarthost_password", None)
    if payload.get("smarthost_password"):
        password = str(payload["smarthost_password"])
        if len(password) > 255 or any(ch in password for ch in "\r\n\0"):
            raise ValueError("The relay password is not valid")
        merged["smarthost_password"] = secret_store.encrypt(password)
    if merged["smarthost_enabled"] and not merged["smarthost_host"]:
        raise ValueError("Enter the relay host, or turn the relay off")
    if "auth_rate_per_hour" in payload:
        merged["auth_rate_per_hour"] = whole("auth_rate_per_hour", 0, 100000, "The mailbox sending limit")
    if "local_rate_per_hour" in payload:
        merged["local_rate_per_hour"] = whole("local_rate_per_hour", 0, 100000, "The website sending limit")
    if "max_message_mb" in payload:
        merged["max_message_mb"] = whole("max_message_mb", 1, 200, "The message size limit")
    if "spam_header_score" in payload:
        merged["spam_header_score"] = score("spam_header_score", "The spam score")
    if "spam_reject_score" in payload:
        merged["spam_reject_score"] = score("spam_reject_score", "The reject score")
    if merged["spam_reject_score"] <= merged["spam_header_score"]:
        raise ValueError("The reject score must be higher than the spam score")
    if "greylisting" in payload:
        merged["greylisting"] = bool(payload["greylisting"])
    if "default_quota_mb" in payload:
        merged["default_quota_mb"] = whole("default_quota_mb", 1, MAX_USER_QUOTA_MB, "The default mailbox size")
    addons._update_state(ADDON_ID, settings=merged)
    if installed():
        apply_settings()
    return current_settings()


def helper_settings() -> dict:
    """What mail-configure receives, with the relay password in clear."""
    stored = _stored_settings()
    values = current_settings()
    relay = None
    if values["smarthost_enabled"] and values["smarthost_host"]:
        relay = {
            "host": values["smarthost_host"],
            "port": int(values["smarthost_port"]),
            "username": values["smarthost_username"],
            "password": secret_store.decrypt(stored.get("smarthost_password")) if values["smarthost_username"] else "",
        }
    return {
        "smarthost": relay,
        "auth_rate_per_hour": int(values["auth_rate_per_hour"]),
        "local_rate_per_hour": int(values["local_rate_per_hour"]),
        "max_message_mb": int(values["max_message_mb"]),
        "spam_header_score": float(values["spam_header_score"]),
        "spam_reject_score": float(values["spam_reject_score"]),
        "greylisting": bool(values["greylisting"]),
    }


def apply_settings() -> None:
    result = shell.privileged("mail-configure", input=json.dumps(helper_settings()), check=False, sensitive=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "mail-configure failed").strip().replace("opanel-helper: ", "")
        raise RuntimeError(f"Saved, but the mail server did not take the settings: {detail}")


def mail_log(lines: int = 100) -> str:
    count = max(1, min(int(lines or 100), 1000))
    result = shell.privileged("mail-log", helper_args=[str(count)], check=False)
    return (result.stdout or "").strip() if result.returncode == 0 else (result.stderr or "").strip()


def queue_size() -> Optional[int]:
    if not installed():
        return None
    result = shell.privileged("mail-queue", check=False)
    text = (result.stdout or "").strip()
    return int(text) if result.returncode == 0 and text.isdigit() else None
