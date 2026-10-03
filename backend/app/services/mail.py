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
import ipaddress
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
from sqlalchemy.orm import Session, object_session

from app.core import secrets as secret_store
from app.core.config import settings
from app.core.access import can_access_owner, scope_owner
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
    if row is None or not can_access_owner(db, actor, row.owner_id):
        raise LookupError("Mail domain not found")
    return row


def get_mailbox(db: Session, actor: User, mailbox_id: int) -> Mailbox:
    row = db.get(Mailbox, mailbox_id)
    if row is None or not can_access_owner(db, actor, row.mail_domain.owner_id):
        raise LookupError("Mailbox not found")
    return row


def get_forwarder(db: Session, actor: User, forwarder_id: int) -> MailForwarder:
    row = db.get(MailForwarder, forwarder_id)
    if row is None or not can_access_owner(db, actor, row.mail_domain.owner_id):
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
    sites = scope_owner(sites, Website.owner_id, db, actor)
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
    _dns_changed(db, [row])
    return row


def _dns_changed(db: Session, rows) -> None:
    """DNS Manager writes these domains' mail records into their zones."""
    from app.services import dns_manager

    dns_manager.mail_domain_changed(db, rows)


def delete_domain(db: Session, actor: User, domain_id: int) -> str:
    row = get_domain(db, actor, domain_id)
    name, webmail, owner_id = row.domain, bool(row.webmail_host), row.owner_id
    db.delete(row)
    db.commit()
    sync(db)
    from app.services import dns_manager

    dns_manager.mail_domain_removed(db, name, owner_id, hostname())
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
    _dns_changed(db, [row])
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
    _dns_changed(db, [row])
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
    if not _is_admin(actor):
        # Like the account's own limit, an administrator's own action is not held to it.
        from app.services import reseller as reseller_pool

        reseller_pool.ensure_room(db, owner, "mailbox")
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
    query = scope_owner(query, MailDomain.owner_id, db, actor)
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
    query = scope_owner(query, MailDomain.owner_id, db, actor)
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
        "relay": row.relay or "",
        "mailboxes": len(row.mailboxes),
        "forwarders": len(row.forwarders),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def overview(db: Session, actor: User) -> dict:
    is_installed = installed()
    domains = db.query(MailDomain)
    domains = scope_owner(domains, MailDomain.owner_id, db, actor)
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
DNS_TYPES = ("TXT", "CNAME", "MX", "A", "AAAA")
MAX_CUSTOM_RECORDS = 20
DEFAULT_DMARC = "v=DMARC1; p=quarantine; adkim=r; aspf=r"
_LABEL_RE = re.compile(r"^(?:@|[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,200}[A-Za-z0-9_])?)$")
_SPF_TOKEN_RE = re.compile(r"^[+~?-]?(?:include|ip4|ip6|a|mx|exists|ptr)(?::[A-Za-z0-9.:/_%{}-]{1,253})?(?:/\d{1,3})?$")


def _dns_custom(row: MailDomain) -> dict:
    try:
        data = json.loads(row.dns_custom or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _fqdn(label: str, domain: str) -> str:
    """A record name as the owner writes it ("@", "mail", "x._domainkey"),
    made absolute within the domain."""
    label = (label or "@").strip().rstrip(".")
    if label in ("@", "", domain):
        return domain
    if label.endswith("." + domain):
        return label
    return f"{label}.{domain}"


def _relative(name: str, domain: str) -> str:
    name = (name or "").strip().rstrip(".").lower()
    if name in ("", "@", domain):
        return "@"
    if name.endswith("." + domain):
        return name[: -len(domain) - 1]
    return name


def normalize_dns_record(item: dict, domain: str = "", template: bool = False) -> dict:
    """One extra record, as an owner or a relay template gives it. A template
    may say {domain} in its value; the name is always inside the domain."""
    if not isinstance(item, dict):
        raise ValueError("A DNS record must have a type, a name and a value")
    rtype = str(item.get("type") or "").strip().upper()
    if rtype not in DNS_TYPES:
        raise ValueError(f"The record type must be one of {', '.join(DNS_TYPES)}")
    label = _relative(str(item.get("name") or "@"), domain) if domain else str(item.get("name") or "@").strip()
    if not _LABEL_RE.fullmatch(label):
        raise ValueError(f"Not a record name: {item.get('name')!r} (use @ for the domain itself)")
    value = str(item.get("value") or "").strip()
    if not value or len(value) > 2048 or any(ch in value for ch in "\r\n\0"):
        raise ValueError("Every record needs a value (one line, at most 2048 characters)")
    check = value.replace("{domain}", domain or "example.com")
    if rtype in ("CNAME", "MX"):
        check = check.rstrip(".").lower()
        if not DOMAIN_RE.fullmatch(check):
            raise ValueError(f"{rtype} must point at a hostname: {value}")
        value = value.rstrip(".").lower()
    elif rtype in ("A", "AAAA"):
        try:
            address = ipaddress.ip_address(check)
        except ValueError as exc:
            raise ValueError(f"Not an IP address: {value}") from exc
        if (rtype == "A") != (address.version == 4):
            raise ValueError(f"{value} is not an {'IPv4' if rtype == 'A' else 'IPv6'} address")
    record = {"type": rtype, "name": label, "value": value}
    if rtype == "MX":
        try:
            priority = int(item.get("priority") if item.get("priority") not in (None, "") else 10)
        except (TypeError, ValueError) as exc:
            raise ValueError("The MX priority must be a whole number") from exc
        if not 0 <= priority <= 65535:
            raise ValueError("The MX priority must be between 0 and 65535")
        record["priority"] = priority
    if template and "{domain}" in label:
        raise ValueError("{domain} goes in the value; the name is relative to the domain already")
    return record


def normalize_spf(value: str) -> str:
    text = " ".join((value or "").split())
    if not text:
        return ""
    tokens = text.split(" ")
    if tokens[0].lower() != "v=spf1" or len(text) > 450:
        raise ValueError("SPF must start with v=spf1 (at most 450 characters)")
    for token in tokens[1:]:
        if token.lower() in ("~all", "-all", "?all", "+all", "all") or token.lower().startswith("redirect="):
            continue
        if not _SPF_TOKEN_RE.fullmatch(token):
            raise ValueError(f"Not an SPF mechanism: {token}")
    return text


def normalize_spf_include(value: str) -> str:
    text = " ".join((value or "").split())
    for token in text.split(" ") if text else []:
        if not _SPF_TOKEN_RE.fullmatch(token):
            raise ValueError(f"Not an SPF mechanism: {token} (for example include:spf.relay.example)")
    if len(text) > 200:
        raise ValueError("The SPF part for the relay is too long")
    return text


def normalize_dmarc(value: str) -> str:
    text = " ".join((value or "").split())
    if not text:
        return ""
    if not text.upper().startswith("V=DMARC1") or len(text) > 450 or any(ch in text for ch in "\r\n\0"):
        raise ValueError("DMARC must start with v=DMARC1 (at most 450 characters)")
    return text


def set_dns_custom(db: Session, actor: User, domain_id: int, payload: dict) -> MailDomain:
    """A domain's own SPF and DMARC values and extra records, set by an
    administrator. Empty SPF or DMARC goes back to what the panel suggests.

    Customers do not edit these: what their domain needs comes from the
    panel's suggestion and the template of the relay it sends through, and
    the customer publishes those records at their DNS provider."""
    if not _is_admin(actor):
        raise PermissionError("Only an administrator changes a domain's mail DNS records")
    row = get_domain(db, actor, domain_id)
    records = payload.get("records") or []
    if not isinstance(records, list) or len(records) > MAX_CUSTOM_RECORDS:
        raise ValueError(f"At most {MAX_CUSTOM_RECORDS} extra records")
    custom = {
        "spf": normalize_spf(payload.get("spf") or ""),
        "dmarc": normalize_dmarc(payload.get("dmarc") or ""),
        "records": [normalize_dns_record(item, row.domain) for item in records],
    }
    row.dns_custom = json.dumps(custom, separators=(",", ":")) if any(custom.values()) else ""
    db.commit()
    _dns_changed(db, [row])
    return row


def effective_relay(row: MailDomain) -> Optional[dict]:
    """The relay this domain's outgoing mail leaves through, or None."""
    choice = row.relay or ""
    if choice == "direct":
        return None
    relays = {relay["id"]: relay for relay in _relays()}
    if choice:
        return relays.get(choice)
    return relays.get(_stored_settings().get("default_relay") or "")


def dns_records(row: MailDomain) -> list[dict]:
    host = hostname()
    try:
        addresses = network.detect_addresses()
    except Exception:  # noqa: BLE001
        addresses = {"ipv4": [], "ipv6": []}
    ipv4, ipv6 = addresses.get("ipv4") or [], addresses.get("ipv6") or []
    relay = effective_relay(row)
    spf = ["v=spf1", "mx", "a"] + [f"ip4:{ip}" for ip in ipv4[:2]] + [f"ip6:{ip}" for ip in ipv6[:1]]
    if relay and relay.get("spf_include"):
        spf += relay["spf_include"].split()
    suggested_spf = " ".join(spf + ["~all"])
    custom = _dns_custom(row)
    records = [
        {"key": "mx", "type": "MX", "name": row.domain, "value": host, "priority": 10},
        {"key": "spf", "type": "TXT", "name": row.domain, "value": custom.get("spf") or suggested_spf,
         "suggested": suggested_spf, "custom": bool(custom.get("spf"))},
        {"key": "dkim", "type": "TXT", "name": f"{DKIM_SELECTOR}._domainkey.{row.domain}",
         "value": f"v=DKIM1; k=rsa; p={row.dkim_public}"},
        {"key": "dmarc", "type": "TXT", "name": f"_dmarc.{row.domain}", "value": custom.get("dmarc") or DEFAULT_DMARC,
         "suggested": DEFAULT_DMARC, "custom": bool(custom.get("dmarc"))},
    ]
    if relay:
        for index, item in enumerate(relay.get("dns_records") or []):
            records.append({
                "key": f"relay-{index}", "type": item["type"], "name": _fqdn(item["name"], row.domain),
                "value": item["value"].replace("{domain}", row.domain), "priority": item.get("priority"),
                "source": "relay", "relay": relay.get("name") or relay["id"],
            })
    for index, item in enumerate(custom.get("records") or []):
        records.append({
            "key": f"custom-{index}", "type": item["type"], "name": _fqdn(item["name"], row.domain),
            "value": item["value"], "priority": item.get("priority"), "source": "custom",
        })
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
        elif rtype == "CNAME":
            out.append(str(item.target).rstrip(".").lower())
        else:
            out.append(item.to_text())
    return out


def _squash(text: str) -> str:
    return "".join((text or "").split()).strip('"').lower()


def _spf_mechanisms(text: str) -> set[str]:
    return {token.lower() for token in (text or "").split()[1:]
            if token.lower() not in ("~all", "-all", "?all", "+all", "all")}


def check_dns(row: MailDomain) -> list[dict]:
    records = dns_records(row)
    for record in records:
        key, rtype, want = record["key"], record["type"], record["value"]
        if key == "spf":
            txt = _resolve(record["name"], "TXT")
            found = None if txt is None else [t for t in txt if t.lower().startswith("v=spf1")]
            # Every mechanism the panel asks for must be there; the owner may
            # have more of their own.
            ok = bool(found) and len(found) == 1 and _spf_mechanisms(want) <= _spf_mechanisms(found[0])
        elif key == "dkim":
            found = _resolve(record["name"], "TXT")
            ok = found is not None and any(row.dkim_public and row.dkim_public in t.replace(" ", "") for t in found)
        elif key == "dmarc":
            txt = _resolve(record["name"], "TXT")
            found = None if txt is None else [t for t in txt if t.upper().startswith("V=DMARC1")]
            ok = bool(found) and (not record.get("custom") or any(_squash(t) == _squash(want) for t in found))
        elif rtype == "TXT":
            found = _resolve(record["name"], "TXT")
            ok = found is not None and any(_squash(t) == _squash(want) for t in found)
        elif rtype in ("MX", "CNAME"):
            found = _resolve(record["name"], rtype)
            ok = found is not None and want.rstrip(".").lower() in found
        else:
            found = _resolve(record["name"], rtype)
            ok = found is not None and want in found
        if found is None:
            record["status"] = "unknown"
        elif ok:
            record["status"] = "ok"
        else:
            record["status"] = "different" if found else "missing"
        record["found"] = found or []
    return records


def _dns_zone(row: MailDomain, actor: User, records: list[dict]) -> Optional[dict]:
    """The DNS Manager zone that carries these records -- the one its writer
    uses -- and whether each record is in it."""
    from app.services import dns_manager

    db = object_session(row)
    zone = dns_manager.mail_zone(db, row) if db is not None else None
    if zone is None or not can_access_owner(db, actor, zone.owner_id):
        return None
    return dns_manager.mail_zone_status(zone, records)


def dns_view(row: MailDomain, actor: User) -> dict:
    custom = _dns_custom(row)
    relay = effective_relay(row)
    records = check_dns(row)
    return {
        "domain": row.domain,
        "dns_zone": _dns_zone(row, actor, records),
        "records": records,
        "custom": {"spf": custom.get("spf") or "", "dmarc": custom.get("dmarc") or "",
                   "records": custom.get("records") or []},
        "can_customize": _is_admin(actor),
        "relay": {
            "choice": row.relay or "",
            "effective": relay["id"] if relay else "",
            "effective_name": (relay.get("name") or relay["id"]) if relay else "",
            # Customers see which relay their mail uses; only an admin picks it.
            "options": [{"id": r["id"], "name": r.get("name") or r["id"]} for r in _relays()]
            if _is_admin(actor) else [],
        },
    }


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
    return f"{webmail_base(row.mail_domain)}/api/auth/sso?token={payload}.{signature}"


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
    relay_ids = {relay["id"] for relay in _relays()}
    state["relay_routes"] = {row.domain: row.relay for row in domains
                             if row.relay == "direct" or row.relay in relay_ids}
    default_relay = _stored_settings().get("default_relay") or ""
    state["default_relay"] = default_relay if default_relay in relay_ids else ""
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


def _store_settings(stored: dict) -> None:
    addons._update_state(ADDON_ID, settings=stored)


def current_settings() -> dict:
    stored = _stored_settings()
    merged = dict(SETTINGS_DEFAULTS)
    for key in SETTINGS_DEFAULTS:
        if key in stored:
            merged[key] = stored[key]
    return merged


def save_settings(payload: dict) -> dict:
    stored = _stored_settings()
    merged = current_settings()

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
    stored.update(merged)
    # The single relay of the first Email release; relays are a list now.
    for old in ("smarthost_enabled", "smarthost_host", "smarthost_port", "smarthost_username", "smarthost_password"):
        stored.pop(old, None)
    _store_settings(stored)
    if installed():
        apply_settings()
    return current_settings()


# ---------------------------------------------------------------------------
# Outgoing relays (smarthosts)
# ---------------------------------------------------------------------------
RELAY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RELAY_TLS = ("starttls", "ssl", "none")
MAX_RELAYS = 20
MAX_RELAY_RECORDS = 10


def _relays() -> list[dict]:
    relays = _stored_settings().get("relays")
    return [dict(r) for r in relays if isinstance(r, dict) and RELAY_ID_RE.fullmatch(str(r.get("id") or ""))] \
        if isinstance(relays, list) else []


def relay_out(relay: dict, db: Optional[Session] = None, default: str = "") -> dict:
    out = {
        "id": relay["id"],
        "name": relay.get("name") or relay["id"],
        "host": relay.get("host") or "",
        "port": int(relay.get("port") or 587),
        "tls": relay.get("tls") or "starttls",
        "username": relay.get("username") or "",
        "password_set": bool(relay.get("password")),
        "spf_include": relay.get("spf_include") or "",
        "dns_records": list(relay.get("dns_records") or []),
        "default": relay["id"] == default,
    }
    if db is not None:
        explicit = [row.domain for row in db.query(MailDomain).filter(MailDomain.relay == relay["id"]).all()]
        out["domains"] = explicit
    return out


def list_relays(db: Session) -> dict:
    default = _stored_settings().get("default_relay") or ""
    return {"relays": [relay_out(r, db, default) for r in _relays()], "default_relay": default}


def save_relay(db: Session, payload: dict, relay_id: Optional[str] = None) -> dict:
    stored = _stored_settings()
    relays = _relays()
    current = next((r for r in relays if r["id"] == relay_id), None) if relay_id else None
    if relay_id and current is None:
        raise LookupError("Relay not found")
    if not relay_id and len(relays) >= MAX_RELAYS:
        raise ValueError(f"At most {MAX_RELAYS} relays")
    relay = dict(current or {})
    name = " ".join(str(payload.get("name") or "").split())
    if not 1 <= len(name) <= 64:
        raise ValueError("Give the relay a name (at most 64 characters)")
    host = str(payload.get("host") or "").strip().lower().rstrip(".")
    if not HOST_RE.fullmatch(host):
        raise ValueError("The relay host must be a hostname such as smtp.example.com, or an IPv4 address")
    try:
        port = int(payload.get("port") or 587)
    except (TypeError, ValueError) as exc:
        raise ValueError("The relay port must be a number") from exc
    if not 1 <= port <= 65535:
        raise ValueError("The relay port must be between 1 and 65535")
    tls = str(payload.get("tls") or ("ssl" if port == 465 else "starttls"))
    if tls not in RELAY_TLS:
        raise ValueError("TLS must be starttls, ssl or none")
    username = str(payload.get("username") or "").strip()
    if len(username) > 255 or any(ch in username for ch in "\r\n\0"):
        raise ValueError("The relay username is not valid")
    relay.update(name=name, host=host, port=port, tls=tls, username=username,
                 spf_include=normalize_spf_include(payload.get("spf_include") or ""))
    records = payload.get("dns_records") or []
    if not isinstance(records, list) or len(records) > MAX_RELAY_RECORDS:
        raise ValueError(f"At most {MAX_RELAY_RECORDS} DNS records for a relay")
    relay["dns_records"] = [normalize_dns_record(item, template=True) for item in records]
    password = payload.get("password")
    if not username:
        relay.pop("password", None)
    elif password:
        password = str(password)
        if len(password) > 255 or any(ch in password for ch in "\r\n\0"):
            raise ValueError("The relay password is not valid")
        relay["password"] = secret_store.encrypt(password)
    elif not relay.get("password"):
        raise ValueError("Enter the relay password, or leave the username empty for a relay without a login")
    # Exim finds a relay's login by its host, so one host carries one login.
    if username and any(r["host"] == host and r.get("username") and r["id"] != relay.get("id") for r in relays):
        raise ValueError(f"Another relay already logs in to {host}")
    if not relay.get("id"):
        relay["id"] = "r" + secrets.token_hex(4)
        relays.append(relay)
    else:
        relays = [relay if r["id"] == relay["id"] else r for r in relays]
    stored["relays"] = relays
    if payload.get("make_default"):
        stored["default_relay"] = relay["id"]
    _store_settings(stored)
    _apply_relays(db)
    return relay_out(relay, db, stored.get("default_relay") or "")


def delete_relay(db: Session, relay_id: str) -> str:
    stored = _stored_settings()
    relays = _relays()
    relay = next((r for r in relays if r["id"] == relay_id), None)
    if relay is None:
        raise LookupError("Relay not found")
    stored["relays"] = [r for r in relays if r["id"] != relay_id]
    if stored.get("default_relay") == relay_id:
        stored["default_relay"] = ""
    # Domains that used it follow the default again.
    for row in db.query(MailDomain).filter(MailDomain.relay == relay_id).all():
        row.relay = ""
    db.commit()
    _store_settings(stored)
    _apply_relays(db)
    return relay.get("name") or relay_id


def set_default_relay(db: Session, relay_id: str) -> str:
    relay_id = relay_id or ""
    if relay_id and relay_id not in {r["id"] for r in _relays()}:
        raise LookupError("Relay not found")
    stored = _stored_settings()
    stored["default_relay"] = relay_id
    _store_settings(stored)
    sync(db)
    _dns_changed(db, db.query(MailDomain).all())
    return relay_id


def set_domain_relay(db: Session, actor: User, domain_id: int, choice: str) -> MailDomain:
    if not _is_admin(actor):
        raise PermissionError("Only an administrator chooses the relay a domain sends through")
    row = get_domain(db, actor, domain_id)
    choice = choice or ""
    if choice not in ("", "direct") and choice not in {r["id"] for r in _relays()}:
        raise LookupError("Relay not found")
    row.relay = choice
    db.commit()
    sync(db)
    _dns_changed(db, [row])
    return row


def _apply_relays(db: Session) -> None:
    if installed():
        apply_settings()
        sync(db)
    # A relay's DNS template reaches every domain that sends through it.
    _dns_changed(db, db.query(MailDomain).all())


def helper_settings() -> dict:
    """What mail-configure receives, relay passwords in clear."""
    values = current_settings()
    relays = []
    for relay in _relays():
        relays.append({
            "id": relay["id"],
            "host": relay.get("host") or "",
            "port": int(relay.get("port") or 587),
            "tls": relay.get("tls") or "starttls",
            "username": relay.get("username") or "",
            "password": secret_store.decrypt(relay.get("password")) if relay.get("username") else "",
        })
    return {
        "relays": relays,
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


# ---------------------------------------------------------------------------
# Logs, queue and Rspamd (administrators)
# ---------------------------------------------------------------------------
def _filter_lines(text: str, q: str, limit: int) -> list[str]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    term = (q or "").strip().lower()
    if term:
        lines = [line for line in lines if term in line.lower()]
    return lines[-limit:]


def mail_log(lines: int = 200, q: str = "") -> list[str]:
    require_installed()
    count = max(1, min(int(lines or 200), 5000))
    # A filter looks further back than the lines it shows.
    fetch = 5000 if q else count
    result = shell.privileged("mail-log", helper_args=[str(fetch)], check=False)
    return _filter_lines(result.stdout if result.returncode == 0 else "", q, count)


def queue_size() -> Optional[int]:
    if not installed():
        return None
    result = shell.privileged("mail-queue", check=False)
    text = (result.stdout or "").strip()
    return int(text) if result.returncode == 0 and text.isdigit() else None


def _rspamd(command: str) -> dict:
    require_installed()
    result = shell.privileged("mail-rspamd", helper_args=[command], check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Rspamd did not answer").strip()
                           .replace("opanel-helper: ", ""))
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise RuntimeError("Rspamd answered with something that is not JSON") from exc
    return data if isinstance(data, dict) else {}


def rspamd_stat() -> dict:
    data = _rspamd("stat")
    actions = data.get("actions") if isinstance(data.get("actions"), dict) else {}
    return {
        "scanned": int(data.get("scanned") or 0),
        "spam": int(data.get("spam_count") or 0),
        "ham": int(data.get("ham_count") or 0),
        "learned": int(data.get("learned") or 0),
        "connections": int(data.get("connections") or 0),
        "uptime": int(data.get("uptime") or 0),
        "version": str(data.get("version") or ""),
        "actions": {str(k): int(v or 0) for k, v in actions.items()},
    }


def _history_row(row: dict) -> dict:
    def first(*keys):
        for key in keys:
            value = row.get(key)
            if value:
                return value
        return ""

    rcpt = first("rcpt_mime", "rcpt_smtp") or []
    if isinstance(rcpt, str):
        rcpt = [rcpt]
    symbols = row.get("symbols") if isinstance(row.get("symbols"), dict) else {}
    ranked = sorted(((name, float((info or {}).get("score") or 0)) for name, info in symbols.items()),
                    key=lambda item: -abs(item[1]))
    stamp = row.get("unix_time") or 0
    try:
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(stamp)))
    except (TypeError, ValueError, OverflowError):
        when = ""
    return {
        "time": when,
        "ip": str(row.get("ip") or ""),
        "from": str(first("sender_mime", "sender_smtp")),
        "to": [str(item) for item in rcpt][:10],
        "subject": str(row.get("subject") or ""),
        "score": round(float(row.get("score") or 0), 2),
        "required": round(float(row.get("required_score") or 0), 2),
        "action": str(row.get("action") or ""),
        "symbols": [{"name": name, "score": round(value, 2)} for name, value in ranked[:15]],
        "size": int(row.get("size") or 0),
        "user": str(row.get("user") or ""),
        "message_id": str(row.get("message-id") or ""),
    }


def rspamd_history(page: int = 1, per_page: int = 50, q: str = "", action: str = "") -> dict:
    rows = [_history_row(r) for r in (_rspamd("history").get("rows") or []) if isinstance(r, dict)]
    term = (q or "").strip().lower()
    if term:
        rows = [r for r in rows if term in " ".join([r["from"], " ".join(r["to"]), r["subject"], r["ip"],
                                                     r["message_id"]]).lower()]
    if action:
        rows = [r for r in rows if r["action"] == action]
    page, per_page = max(1, int(page or 1)), max(1, min(int(per_page or 50), 200))
    return {"items": rows[(page - 1) * per_page: page * per_page], "total": len(rows), "page": page,
            "per_page": per_page}


def rspamd_log(lines: int = 300, q: str = "") -> list[str]:
    require_installed()
    count = max(1, min(int(lines or 300), 5000))
    fetch = 5000 if q else count
    result = shell.privileged("mail-rspamd", helper_args=["log", str(fetch)], check=False)
    return _filter_lines(result.stdout if result.returncode == 0 else "", q, count)
