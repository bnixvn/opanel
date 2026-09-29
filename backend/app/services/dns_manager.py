"""DNS Manager addon: authoritative zones on this server (PowerDNS).

The panel keeps who owns which zone (``DnsZone``); the records live in
PowerDNS, which the panel edits through its HTTP API on 127.0.0.1 with the key
the addon's installer left for the panel user. Customers manage the zones of
their own websites' domains; administrators every zone.

Records are also kept in step with the rest of the panel, never replacing
what an owner set by hand where that can be avoided:
- a new website gets A/AAAA records for its name and www (a zone is created
  for it when the setting says so);
- a mail domain's records (MX, SPF, DKIM, DMARC, webmail, the relay's DNS
  template) are written into its zone;
- a wildcard certificate can be issued over DNS-01 in the zone (the helper's
  certbot-dns-local).
Every hook is a no-op while the addon is not installed.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import is_admin_role
from app.models.entities import DnsZone, MailDomain, User, Website
from app.services import addons, network, panel_settings

log = logging.getLogger("opanel.dns")

ADDON_ID = "dns"
MARKER = Path("/etc/opanel-dns/installed")
KEY_FILE = Path("/var/lib/opanel/addons/dns-api.key")
API_BASE = "http://127.0.0.1:8081/api/v1/servers/localhost"
RECORD_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV", "CAA")
TTL_CHOICES = (60, 300, 900, 1800, 3600, 14400, 43200, 86400)
MAX_RECORDS = 1000

DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
HOST_RE = re.compile(r"^(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9])?)(?:\.[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9])?)*$")
LABEL_RE = re.compile(r"^(?:@|\*|(?:\*\.)?[a-z0-9_](?:[a-z0-9_.-]{0,200}[a-z0-9_])?)$")

SETTINGS_DEFAULTS = {"ns1": "", "ns2": "", "hostmaster": "", "default_ttl": 3600}


class NotInstalled(RuntimeError):
    """The DNS Manager addon is not installed on this server."""


# ---------------------------------------------------------------------------
# Server and settings
# ---------------------------------------------------------------------------
def installed() -> bool:
    try:
        return MARKER.exists()
    except OSError:
        return False


def require_installed() -> None:
    if not installed():
        raise NotInstalled("The DNS Manager addon is not installed. An administrator can install it from Settings › Addons.")


def _panel_host() -> str:
    host = (settings.panel_domain or "").strip().lower()
    if not DOMAIN_RE.fullmatch(host):
        url = panel_settings.current_settings().get("panel_url") or settings.panel_url or ""
        host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host if DOMAIN_RE.fullmatch(host) else ""


def current_settings() -> dict:
    stored = addons._entry(ADDON_ID).get("settings")
    stored = stored if isinstance(stored, dict) else {}
    values = {key: stored.get(key, default) for key, default in SETTINGS_DEFAULTS.items()}
    host = _panel_host()
    values["ns1"] = values["ns1"] or (f"ns1.{host}" if host else "")
    values["ns2"] = values["ns2"] or (f"ns2.{host}" if host else "")
    values["hostmaster"] = values["hostmaster"] or (f"hostmaster@{host}" if host else "")
    return values


def server_addresses() -> dict:
    try:
        found = network.detect_addresses()
    except Exception:  # noqa: BLE001
        found = {"ipv4": [], "ipv6": []}
    ipv6 = found.get("ipv6") or []
    try:
        if not panel_settings.current_settings().get("ipv6_enabled", True):
            ipv6 = []
    except Exception:  # noqa: BLE001
        pass
    return {"ipv4": (found.get("ipv4") or [])[:1], "ipv6": ipv6[:1]}


def save_settings(db: Session, payload: dict) -> dict:
    stored = addons._entry(ADDON_ID).get("settings")
    stored = dict(stored) if isinstance(stored, dict) else {}
    for key in ("ns1", "ns2"):
        if key in payload:
            value = str(payload[key] or "").strip().lower().rstrip(".")
            if value and not DOMAIN_RE.fullmatch(value):
                raise ValueError(f"{key} must be a hostname such as {key}.example.com")
            stored[key] = value
    if "hostmaster" in payload:
        value = str(payload["hostmaster"] or "").strip().lower()
        if value and not re.fullmatch(r"[a-z0-9._%+-]{1,64}@[a-z0-9.-]+\.[a-z]{2,63}", value):
            raise ValueError("The hostmaster must be an email address")
        stored["hostmaster"] = value
    if "default_ttl" in payload:
        ttl = int(payload["default_ttl"])
        if ttl not in TTL_CHOICES:
            raise ValueError("Pick one of the offered TTLs")
        stored["default_ttl"] = ttl
    stored.pop("auto_zone", None)
    before = current_settings()
    addons._update_state(ADDON_ID, settings=stored)
    after = current_settings()
    if installed() and (before["ns1"], before["ns2"], before["hostmaster"]) != (after["ns1"], after["ns2"], after["hostmaster"]):
        # Every zone carries the nameservers in its NS and SOA records.
        for zone in db.query(DnsZone).all():
            try:
                _write_apex(zone.name)
            except RuntimeError as exc:
                log.warning("could not update NS of %s: %s", zone.name, exc)
    return after


# ---------------------------------------------------------------------------
# The PowerDNS API
# ---------------------------------------------------------------------------
def _api(method: str, path: str, body=None):
    require_installed()
    try:
        key = KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("The DNS server's API key is missing; reinstall DNS Manager") from exc
    request = urllib.request.Request(API_BASE + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            text = response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("error") or ""
        except ValueError:
            detail = ""
        if exc.code == 404:
            raise LookupError(detail or "Not found on the DNS server") from exc
        raise ValueError(detail or f"The DNS server refused the change (HTTP {exc.code})") from exc
    except OSError as exc:
        raise RuntimeError(f"The DNS server is not answering: {exc}") from exc
    return json.loads(text) if text else None


def _abs(name: str) -> str:
    return name.rstrip(".").lower() + "."


def _get_zone(name: str) -> dict:
    return _api("GET", f"/zones/{_abs(name)}")


def _patch(zone: str, rrsets: list[dict]) -> None:
    if rrsets:
        _api("PATCH", f"/zones/{_abs(zone)}", {"rrsets": rrsets})


def _rrset(zone_data: dict, name: str, rtype: str) -> Optional[dict]:
    for rrset in zone_data.get("rrsets", []):
        if rrset["name"] == _abs(name) and rrset["type"] == rtype:
            return rrset
    return None


def _values(zone_data: dict, name: str, rtype: str) -> list[str]:
    rrset = _rrset(zone_data, name, rtype)
    return [r["content"] for r in rrset["records"]] if rrset else []


def _replace(name: str, rtype: str, contents: list[str], ttl: int) -> dict:
    if not contents:
        return {"name": _abs(name), "type": rtype, "changetype": "DELETE", "records": []}
    return {"name": _abs(name), "type": rtype, "ttl": int(ttl), "changetype": "REPLACE",
            "records": [{"content": c, "disabled": False} for c in contents]}


# ---------------------------------------------------------------------------
# Record contents
# ---------------------------------------------------------------------------
def _fqdn(host: str, zone: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    if host in ("", "@"):
        return _abs(zone)
    if not HOST_RE.fullmatch(host):
        raise ValueError(f"Not a hostname: {host}")
    return host + "."


def _txt(text: str) -> str:
    """TXT content: quoted strings of at most 250 bytes each (DNS allows 255),
    which resolvers join back into one value."""
    raw = (text or "").strip()
    if raw.startswith('"'):
        raw = _untxt(raw)
    if not raw or len(raw) > 4000 or any(ch in raw for ch in "\r\n\0"):
        raise ValueError("TXT needs a value on one line (at most 4000 characters)")
    chunks, current = [], ""
    for char in raw:
        if len((current + char).encode()) > 250:
            chunks.append(current)
            current = ""
        current += char
    chunks.append(current)
    return " ".join('"%s"' % chunk.replace("\\", "\\\\").replace('"', '\\"') for chunk in chunks)


def _untxt(content: str) -> str:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', content or "")
    text = "".join(parts) if parts else content
    return text.replace('\\"', '"').replace("\\\\", "\\")


def to_content(rtype: str, value: str, priority, zone: str) -> str:
    rtype = (rtype or "").upper()
    value = (value or "").strip()
    if rtype == "A":
        try:
            return str(ipaddress.IPv4Address(value))
        except ValueError as exc:
            raise ValueError(f"Not an IPv4 address: {value}") from exc
    if rtype == "AAAA":
        try:
            return str(ipaddress.IPv6Address(value))
        except ValueError as exc:
            raise ValueError(f"Not an IPv6 address: {value}") from exc
    if rtype in ("CNAME", "NS"):
        return _fqdn(value, zone)
    if rtype == "MX":
        return f"{_priority(priority)} {_fqdn(value, zone)}"
    if rtype == "TXT":
        return _txt(value)
    if rtype == "SRV":
        parts = value.split()
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError("SRV value is: weight port target (for example 5 5060 sip.example.com)")
        weight, port = int(parts[0]), int(parts[1])
        if not 0 <= weight <= 65535 or not 0 <= port <= 65535:
            raise ValueError("SRV weight and port must be between 0 and 65535")
        return f"{_priority(priority)} {weight} {port} {_fqdn(parts[2], zone)}"
    if rtype == "CAA":
        match = re.fullmatch(r"(issue|issuewild|iodef)\s+\"?([^\"\s]{1,253})\"?", value)
        if not match:
            raise ValueError('CAA value is: issue|issuewild|iodef value (for example issue letsencrypt.org)')
        return f'0 {match.group(1)} "{match.group(2)}"'
    raise ValueError(f"Record type must be one of {', '.join(RECORD_TYPES)}")


def _priority(value) -> int:
    try:
        number = int(value if value not in (None, "") else 10)
    except (TypeError, ValueError) as exc:
        raise ValueError("The priority must be a whole number") from exc
    if not 0 <= number <= 65535:
        raise ValueError("The priority must be between 0 and 65535")
    return number


def from_content(rtype: str, content: str) -> dict:
    """What the page shows and edits: value, and priority where the type has one."""
    if rtype == "TXT":
        return {"value": _untxt(content), "priority": None}
    if rtype == "MX":
        priority, _, host = content.partition(" ")
        return {"value": host.rstrip("."), "priority": int(priority) if priority.isdigit() else None}
    if rtype == "SRV":
        parts = content.split()
        if len(parts) == 4:
            return {"value": f"{parts[1]} {parts[2]} {parts[3].rstrip('.')}", "priority": int(parts[0])}
    if rtype == "CAA":
        match = re.fullmatch(r"\d+\s+(\S+)\s+\"?([^\"]*)\"?", content)
        if match:
            return {"value": f"{match.group(1)} {match.group(2)}", "priority": None}
    if rtype in ("CNAME", "NS"):
        return {"value": content.rstrip("."), "priority": None}
    return {"value": content, "priority": None}


def _name_in_zone(label: str, zone: str) -> str:
    label = (label or "@").strip().lower()
    # A trailing dot names the host absolutely, so it has to sit in this zone.
    if label.endswith(".") and label.rstrip(".") != zone and not label.rstrip(".").endswith("." + zone):
        raise ValueError(f"{label} is outside the zone {zone}")
    label = label.rstrip(".")
    if label in ("", "@", zone):
        return zone
    if label.endswith("." + zone):
        label = label[: -len(zone) - 1]
    if not LABEL_RE.fullmatch(label):
        raise ValueError(f"Not a record name: {label!r} (use @ for the domain itself)")
    return f"{label}.{zone}"


def _relative(name: str, zone: str) -> str:
    name = name.rstrip(".")
    return "@" if name == zone else name[: -len(zone) - 1] if name.endswith("." + zone) else name


# ---------------------------------------------------------------------------
# Zones and ownership
# ---------------------------------------------------------------------------
def _is_admin(user: User) -> bool:
    return is_admin_role(user.role)


def normalize_domain(value: str) -> str:
    name = (value or "").strip().lower().rstrip(".")
    if name.startswith("www."):
        name = name[4:]
    if not DOMAIN_RE.fullmatch(name) or len(name) > 253:
        raise ValueError("Enter a domain name such as example.com")
    return name


def get_zone(db: Session, actor: User, zone_id: int) -> DnsZone:
    row = db.get(DnsZone, zone_id)
    if row is None or (not _is_admin(actor) and row.owner_id != actor.id):
        raise LookupError("Zone not found")
    return row


def _owned_names(db: Session, owner_id: Optional[int] = None) -> dict[str, int]:
    """Website and alias names, each with the account that owns it."""
    names: dict[str, int] = {}
    query = db.query(Website)
    if owner_id is not None:
        query = query.filter(Website.owner_id == owner_id)
    for site in query.all():
        names[(site.domain or "").lower()] = site.owner_id
        for alias in site.aliases or []:
            names[(alias.domain or "").lower()] = site.owner_id
    return names


def zone_for_name(db: Session, name: str, owner_id: Optional[int] = None) -> Optional[DnsZone]:
    """The closest hosted zone a name falls in, optionally only the owner's."""
    labels = name.rstrip(".").lower().split(".")
    for index in range(len(labels) - 1):
        candidate = ".".join(labels[index:])
        row = db.query(DnsZone).filter(DnsZone.name == candidate).first()
        if row is not None:
            return row if owner_id is None or row.owner_id == owner_id else None
    return None


def _soa(zone: str) -> str:
    values = current_settings()
    mname = _abs(values["ns1"] or f"ns1.{zone}")
    local, _, host = (values["hostmaster"] or f"hostmaster@{zone}").partition("@")
    rname = _abs(local.replace(".", "\\.") + "." + host)
    return f"{mname} {rname} 1 10800 3600 604800 3600"


def _ns_records() -> list[str]:
    values = current_settings()
    return [_abs(ns) for ns in (values["ns1"], values["ns2"]) if ns]


def _write_apex(zone: str) -> None:
    """NS and SOA from the settings; glue for nameservers inside the zone."""
    data = _get_zone(zone)
    serial = "1"
    current = _values(data, zone, "SOA")
    if current:
        parts = current[0].split()
        serial = parts[2] if len(parts) >= 3 else "1"
    soa = _soa(zone).split()
    soa[2] = serial
    ttl = int(current_settings()["default_ttl"])
    rrsets = [_replace(zone, "SOA", [" ".join(soa)], 3600), _replace(zone, "NS", _ns_records(), 86400)]
    rrsets += _glue(zone, data)
    _patch(zone, rrsets)


def _glue(zone: str, data: Optional[dict] = None) -> list[dict]:
    addresses = server_addresses()
    rrsets = []
    for ns in _ns_records():
        host = ns.rstrip(".")
        if host == zone or host.endswith("." + zone):
            for rtype, values in (("A", addresses["ipv4"]), ("AAAA", addresses["ipv6"])):
                if values and (data is None or not _values(data, host, rtype)):
                    rrsets.append(_replace(host, rtype, values, 3600))
    return rrsets


def create_zone(db: Session, actor: User, domain: str, owner_id: Optional[int] = None,
                seed: bool = True) -> DnsZone:
    require_installed()
    name = normalize_domain(domain)
    if db.query(DnsZone).filter(DnsZone.name == name).first() is not None:
        raise ValueError(f"{name} already has a zone on this server")
    owners = _owned_names(db)
    owner_of_site = owners.get(name) or owners.get(f"www.{name}")
    if _is_admin(actor):
        owner = db.get(User, owner_id) if owner_id else (db.get(User, owner_of_site) if owner_of_site else actor)
        if owner is None:
            raise ValueError("That account does not exist")
    else:
        if owner_of_site != actor.id:
            raise PermissionError("You can only add a zone for the domains of your own websites")
        owner = actor
    ttl = int(current_settings()["default_ttl"])
    if not _ns_records():
        raise ValueError("Set the nameservers in DNS Manager's settings first")
    body = {"name": _abs(name), "kind": "Native", "soa_edit_api": "DEFAULT", "nameservers": [],
            "rrsets": [_replace(name, "SOA", [_soa(name)], 3600), _replace(name, "NS", _ns_records(), 86400)]}
    _api("POST", "/zones", body)
    row = DnsZone(name=name, owner_id=owner.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    if seed:
        seed_zone(db, row, ttl)
    return row


def seed_zone(db: Session, zone: DnsZone, ttl: Optional[int] = None) -> None:
    """Glue, then the records of the owner's websites and mail -- those whose
    closest zone this is (a sub-zone of their own carries its names)."""
    try:
        _patch(zone.name, _glue(zone.name, _get_zone(zone.name)))
        for site in db.query(Website).filter(Website.owner_id == zone.owner_id).all():
            for host in [site.domain] + [a.domain for a in site.aliases or []]:
                host = (host or "").lower()
                closest = zone_for_name(db, host, zone.owner_id)
                if closest is not None and closest.id == zone.id:
                    _ensure_host_records(zone.name, host)
        for mail_domain in db.query(MailDomain).filter(MailDomain.owner_id == zone.owner_id).all():
            closest = mail_zone(db, mail_domain)
            if closest is not None and closest.id == zone.id:
                _write_mail_records(db, zone, mail_domain)
    except (RuntimeError, ValueError, LookupError) as exc:
        log.warning("could not add the default records of %s: %s", zone.name, exc)


def _panel_names(db: Session) -> set[str]:
    """Every website and alias name on the panel, without a leading www."""
    return {name[4:] if name.startswith("www.") else name for name in _owned_names(db) if name}


def _on_panel(name: str, panel_names: set[str]) -> bool:
    return any(n == name or n.endswith("." + name) for n in panel_names)


def delete_zone(db: Session, actor: User, zone_id: int) -> str:
    row = get_zone(db, actor, zone_id)
    name = row.name
    # Every domain on the panel has DNS; a zone goes by hand only once no
    # website or alias uses it any more.
    if _on_panel(name, _panel_names(db)):
        raise ValueError("This zone is in use by a website on the panel; it stays while the website does")
    try:
        _api("DELETE", f"/zones/{_abs(name)}")
    except LookupError:
        pass
    db.delete(row)
    db.commit()
    return name


def delete_for_owner(db: Session, owner: User) -> list[str]:
    """An account is being deleted: its zones go with it. The caller commits."""
    names = []
    for row in db.query(DnsZone).filter(DnsZone.owner_id == owner.id).all():
        names.append(row.name)
        if installed():
            try:
                _api("DELETE", f"/zones/{_abs(row.name)}")
            except (LookupError, RuntimeError, ValueError) as exc:
                log.warning("could not delete zone %s: %s", row.name, exc)
        db.delete(row)
    db.flush()
    return names


def zone_out(row: DnsZone, panel_names: Optional[set[str]] = None) -> dict:
    out = {"id": row.id, "name": row.name, "owner_id": row.owner_id,
           "owner": row.owner.username if row.owner else "",
           "created_at": row.created_at.isoformat() if row.created_at else None}
    if panel_names is not None:
        out["on_panel"] = _on_panel(row.name, panel_names)
    return out


def list_zones(db: Session, actor: User, q: str = "", page: int = 1, per_page: int = 50) -> dict:
    query = db.query(DnsZone)
    if not _is_admin(actor):
        query = query.filter(DnsZone.owner_id == actor.id)
    term = (q or "").strip().lower()
    if term:
        query = query.filter(DnsZone.name.like(f"%{term}%"))
    total = query.count()
    page, per_page = max(1, int(page or 1)), max(1, min(int(per_page or 50), 200))
    rows = query.order_by(DnsZone.name).offset((page - 1) * per_page).limit(per_page).all()
    names = _panel_names(db)
    return {"items": [zone_out(r, names) for r in rows], "total": total, "page": page, "per_page": per_page}


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
def records(db: Session, actor: User, zone_id: int) -> dict:
    row = get_zone(db, actor, zone_id)
    data = _get_zone(row.name)
    # Which values the Email addon keeps here, and for which mail domain.
    by_mail: dict[tuple[str, str, str], str] = {}
    spf_of: dict[str, str] = {}
    for key, entry in _managed(row).items():
        if not key.startswith("mail:") or not isinstance(entry, dict):
            continue
        for item in entry.get("records", []):
            if len(item) == 3:
                by_mail[tuple(item)] = key[5:]
        if entry.get("spf"):
            spf_of[key[5:]] = key[5:]
    items = []
    for rrset in sorted(data.get("rrsets", []), key=lambda r: (r["name"].count("."), r["name"], r["type"])):
        name = rrset["name"].rstrip(".")
        rtype = rrset["type"]
        locked = rtype == "SOA" or (rtype == "NS" and name == row.name)
        for record in rrset["records"]:
            shown = from_content(rtype, record["content"])
            mail_of = by_mail.get((name, rtype, record["content"])) or (
                spf_of.get(name) if rtype == "TXT" and _is_spf(record["content"]) else None)
            items.append({"name": _relative(name, row.name), "fqdn": name, "type": rtype, "ttl": rrset.get("ttl"),
                          "value": shown["value"] if rtype != "SOA" else record["content"],
                          "priority": shown["priority"], "content": record["content"], "locked": locked,
                          "mail": mail_of})
    return {"zone": zone_out(row, _panel_names(db)), "records": items,
            "nameservers": [ns.rstrip(".") for ns in _ns_records()],
            "addresses": server_addresses()}


def _check_name(zone: DnsZone, name: str, rtype: str) -> str:
    fqdn = _name_in_zone(name, zone.name)
    if rtype == "NS" and fqdn == zone.name:
        raise ValueError("The domain's own NS records follow DNS Manager's settings")
    if rtype == "CNAME" and fqdn == zone.name:
        raise ValueError("The domain itself cannot be a CNAME; use A/AAAA records")
    return fqdn


def _cname_conflict(data: dict, fqdn: str, rtype: str, ignore: tuple = ()) -> None:
    """A CNAME is the only record of its name; PowerDNS refuses anything else
    with a message no customer should have to read."""
    others = {r["type"] for r in data.get("rrsets", []) if r["name"] == _abs(fqdn) and r["records"]} - set(ignore)
    if rtype == "CNAME" and others - {"CNAME"}:
        raise ValueError("A CNAME must be the only record of its name, and that name has other records")
    if rtype != "CNAME" and "CNAME" in others:
        raise ValueError("That name is a CNAME, which can have no other record")


def add_record(db: Session, actor: User, zone_id: int, item: dict) -> dict:
    zone = get_zone(db, actor, zone_id)
    rtype = str(item.get("type") or "").upper()
    fqdn = _check_name(zone, item.get("name"), rtype)
    content = to_content(rtype, item.get("value"), item.get("priority"), zone.name)
    ttl = _ttl(item.get("ttl"))
    data = _get_zone(zone.name)
    if sum(len(r["records"]) for r in data.get("rrsets", [])) >= MAX_RECORDS:
        raise ValueError(f"A zone holds at most {MAX_RECORDS} records")
    current = _values(data, fqdn, rtype)
    if content in current:
        raise ValueError("That record already exists")
    if rtype == "CNAME" and current:
        raise ValueError("That name already has a CNAME record; edit it instead")
    _cname_conflict(data, fqdn, rtype)
    _patch(zone.name, [_replace(fqdn, rtype, current + [content], ttl)])
    return records(db, actor, zone_id)


def delete_record(db: Session, actor: User, zone_id: int, item: dict) -> dict:
    zone = get_zone(db, actor, zone_id)
    rtype = str(item.get("type") or "").upper()
    fqdn = _check_name(zone, item.get("name"), rtype)
    if rtype == "SOA":
        raise ValueError("The SOA record is managed by DNS Manager")
    data = _get_zone(zone.name)
    rrset = _rrset(data, fqdn, rtype)
    content = str(item.get("content") or "")
    if rrset is None or content not in [r["content"] for r in rrset["records"]]:
        raise LookupError("That record is not in the zone any more")
    values = [r["content"] for r in rrset["records"] if r["content"] != content]
    _patch(zone.name, [_replace(fqdn, rtype, values, rrset.get("ttl") or 3600)])
    return records(db, actor, zone_id)


def update_record(db: Session, actor: User, zone_id: int, old: dict, new: dict) -> dict:
    zone = get_zone(db, actor, zone_id)
    old_type = str(old.get("type") or "").upper()
    new_type = str(new.get("type") or "").upper()
    old_fqdn = _check_name(zone, old.get("name"), old_type)
    new_fqdn = _check_name(zone, new.get("name"), new_type)
    content = to_content(new_type, new.get("value"), new.get("priority"), zone.name)
    ttl = _ttl(new.get("ttl"))
    data = _get_zone(zone.name)
    old_rrset = _rrset(data, old_fqdn, old_type)
    old_content = str(old.get("content") or "")
    if old_rrset is None or old_content not in [r["content"] for r in old_rrset["records"]]:
        raise LookupError("That record is not in the zone any more")
    changes = []
    if (old_fqdn, old_type) == (new_fqdn, new_type):
        values = [content if r["content"] == old_content else r["content"] for r in old_rrset["records"]]
        changes.append(_replace(new_fqdn, new_type, list(dict.fromkeys(values)), ttl))
    else:
        remaining = [r["content"] for r in old_rrset["records"] if r["content"] != old_content]
        _cname_conflict(data, new_fqdn, new_type, ignore=(old_type,) if old_fqdn == new_fqdn and not remaining else ())
        changes.append(_replace(old_fqdn, old_type, remaining, old_rrset.get("ttl") or 3600))
        target = _values(data, new_fqdn, new_type)
        values = [content] if new_type == "CNAME" else list(dict.fromkeys(target + [content]))
        changes.append(_replace(new_fqdn, new_type, values, ttl))
    _patch(zone.name, changes)
    return records(db, actor, zone_id)


def _ttl(value) -> int:
    try:
        ttl = int(value if value not in (None, "") else current_settings()["default_ttl"])
    except (TypeError, ValueError) as exc:
        raise ValueError("TTL must be a number of seconds") from exc
    if not 60 <= ttl <= 604800:
        raise ValueError("TTL must be between 60 seconds and 7 days")
    return ttl


def restore_defaults(db: Session, actor: User, zone_id: int) -> dict:
    """Put back the records the panel adds: glue, websites and mail."""
    zone = get_zone(db, actor, zone_id)
    _write_apex(zone.name)
    seed_zone(db, zone)
    return records(db, actor, zone_id)


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------
def delegation(zone: DnsZone) -> dict:
    want = sorted(ns.rstrip(".") for ns in _ns_records())
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return {"status": "unknown", "expected": want, "found": []}
    resolver = dns.resolver.Resolver()
    resolver.timeout = 3
    resolver.lifetime = 5
    try:
        answer = resolver.resolve(zone.name, "NS")
        found = sorted(str(item.target).rstrip(".").lower() for item in answer)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        found = []
    except (dns.exception.DNSException, OSError):
        return {"status": "unknown", "expected": want, "found": []}
    status = "ok" if found and set(want) <= set(found) else ("different" if found else "missing")
    return {"status": status, "expected": want, "found": found}


# ---------------------------------------------------------------------------
# Hooks: websites, mail, accounts
# ---------------------------------------------------------------------------
def _ensure_host_records(zone: str, host: str) -> None:
    """A/AAAA for a site name and its www, unless the owner put something
    else there already."""
    data = _get_zone(zone)
    addresses = server_addresses()
    changes = []
    names = [host] + ([f"www.{host}"] if not host.startswith("www.") else [])
    for name in names:
        if _values(data, name, "CNAME"):
            continue
        for rtype, values in (("A", addresses["ipv4"]), ("AAAA", addresses["ipv6"])):
            if values and not _values(data, name, rtype):
                changes.append(_replace(name, rtype, values, int(current_settings()["default_ttl"])))
    _patch(zone, changes)


def website_created(db: Session, website: Website, hosts: Optional[list[str]] = None) -> None:
    """A new website (or alias): records in the owner's zone, and a zone of
    its own when the setting asks for one. Never fails the caller."""
    if not installed():
        return
    try:
        for host in hosts or [website.domain] + [a.domain for a in website.aliases or []]:
            host = (host or "").lower()
            zone = zone_for_name(db, host, website.owner_id)
            # Every domain on the panel has DNS: a zone of its own, unless it
            # falls in a zone already (another account's parent zone included --
            # a sub-zone there would take over names that account answers for).
            if zone is None and zone_for_name(db, host) is None:
                owner = db.get(User, website.owner_id)
                zone = create_zone(db, owner, host[4:] if host.startswith("www.") else host,
                                   owner_id=website.owner_id) if owner else None
            if zone is not None:
                _ensure_host_records(zone.name, host)
    except Exception as exc:  # noqa: BLE001 - DNS must never stop a website
        log.warning("DNS records for %s were not added: %s", website.domain, exc)


def website_removed(db: Session, hosts: list[str], owner_id: int) -> None:
    """A website or alias is gone: take away the A/AAAA records the panel
    added for its names -- only values that still point at this server, and
    not for a name another website still serves. The zone stays. Never fails
    the caller."""
    if not installed():
        return
    try:
        served = set()
        for name in _owned_names(db):
            served.update({name, name[4:] if name.startswith("www.") else f"www.{name}"})
        addresses = server_addresses()
        ours = {"A": set(addresses["ipv4"]), "AAAA": set(addresses["ipv6"])}
        for host in hosts:
            host = (host or "").lower()
            zone = zone_for_name(db, host, owner_id)
            if zone is None:
                continue
            data = _get_zone(zone.name)
            changes = []
            for name in [host] + ([f"www.{host}"] if not host.startswith("www.") else []):
                if name in served:
                    continue
                for rtype in ("A", "AAAA"):
                    current = _values(data, name, rtype)
                    keep = [value for value in current if value not in ours[rtype]]
                    if keep != current:
                        changes.append(_replace(name, rtype, keep, (_rrset(data, name, rtype) or {}).get("ttl") or 3600))
            if changes:
                _patch(zone.name, changes)
    except Exception as exc:  # noqa: BLE001 - DNS must never stop a website
        log.warning("DNS records of %s were not removed: %s", ", ".join(hosts), exc)


# ---------------------------------------------------------------------------
# Email: a mail domain's records, kept in its zone
# ---------------------------------------------------------------------------
SPF_ENDINGS = ("~all", "-all", "?all", "+all", "all")


def _managed(zone: DnsZone) -> dict:
    try:
        data = json.loads(zone.managed or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_managed(db: Session, zone: DnsZone, key: str, entry: Optional[dict]) -> None:
    data = _managed(zone)
    if entry:
        data[key] = entry
    else:
        data.pop(key, None)
    zone.managed = json.dumps(data, separators=(",", ":"), sort_keys=True) if data else ""
    db.commit()


def mail_zone(db: Session, mail_domain) -> Optional[DnsZone]:
    """The zone that carries a mail domain's records: the closest zone hosted
    here, and only if it is the mail domain's own account's. The writer and
    the Email page both go by this."""
    if not installed():
        return None
    return zone_for_name(db, mail_domain.domain, mail_domain.owner_id)


def _spf_parts(text: str) -> tuple[list[str], str]:
    """An SPF record's mechanisms in order, and its closing all ("" if none)."""
    tokens = _untxt(text or "").split()[1:]
    mechanisms = [token for token in tokens if token.lower() not in SPF_ENDINGS]
    ending = next((token for token in tokens if token.lower() in SPF_ENDINGS), "")
    return mechanisms, ending


def _is_spf(content: str) -> bool:
    return _untxt(content).lower().startswith("v=spf1")


def _is_dmarc(content: str) -> bool:
    return _untxt(content).lower().startswith("v=dmarc1")


def _mail_desired(zone: str, mail_domain) -> list[dict]:
    """The records the Email page lists for the domain, in PowerDNS form."""
    from app.services import mail

    out = []
    for record in mail.dns_records(mail_domain):
        name = record["name"].lower().rstrip(".")
        if name != zone and not name.endswith("." + zone):
            continue
        rtype = record["type"].upper()
        try:
            content = to_content(rtype, record["value"], record.get("priority"), zone)
        except ValueError:
            continue
        out.append({"key": record["key"], "name": name, "type": rtype, "content": content,
                    "value": record["value"], "custom": bool(record.get("custom"))})
    return out


def _write_mail_records(db: Session, zone: DnsZone, mail_domain) -> None:
    """Put the mail domain's records in the zone, and take out the values the
    panel wrote here before and no longer asks for. The owner's own records
    stay: mechanisms they added to the SPF, their own DMARC policy, another
    MX, their own address for webmail.<domain>."""
    key = f"mail:{mail_domain.domain}"
    before = _managed(zone).get(key)
    tracked = isinstance(before, dict)
    before = before if tracked else {}
    previous = {tuple(item) for item in before.get("records", []) if len(item) == 3}
    desired = _mail_desired(zone.name, mail_domain)
    data = _get_zone(zone.name)
    default_ttl = int(current_settings()["default_ttl"])
    work: dict[tuple[str, str], list[str]] = {}
    original: dict[tuple[str, str], list[str]] = {}
    ttls: dict[tuple[str, str], int] = {}

    def values(name: str, rtype: str) -> list[str]:
        slot = (name, rtype)
        if slot not in work:
            work[slot] = _values(data, name, rtype)
            original[slot] = list(work[slot])
            ttls[slot] = (_rrset(data, name, rtype) or {}).get("ttl") or default_ttl
        return work[slot]

    def peek(name: str, rtype: str) -> list[str]:
        return work[(name, rtype)] if (name, rtype) in work else _values(data, name, rtype)

    # What the panel wrote before and asks for no more: another relay's
    # records, an extra record the administrator removed, an old MX host.
    wanted = {(item["name"], item["type"], item["content"]) for item in desired if item["key"] != "spf"}
    for name, rtype, content in previous - wanted:
        current = values(name, rtype)
        if content in current:
            current.remove(content)

    written: list[list[str]] = []
    spf_asked = ""
    for item in desired:
        name, rtype, content, slot = item["name"], item["type"], item["content"], (item["name"], item["type"])
        current = values(name, rtype)
        if item["key"] == "spf":
            # One SPF record: the panel's mechanisms, then whatever the owner
            # added of their own, with their ending. An administrator's custom
            # SPF is taken as it is.
            spf = [value for value in current if _is_spf(value)]
            text = item["value"]
            if spf and tracked and not item["custom"]:
                asked, asked_ending = _spf_parts(item["value"])
                ours = {m.lower() for m in asked} | {m.lower() for m in _spf_parts(before.get("spf", ""))[0]}
                have, have_ending = _spf_parts(spf[0])
                text = " ".join(["v=spf1"] + asked + [m for m in have if m.lower() not in ours]
                                + [have_ending or asked_ending])
            work[slot] = [value for value in current if value not in spf] + [_txt(text)]
            spf_asked = item["value"]
            continue
        if item["key"] == "dmarc":
            dmarc = [value for value in current if _is_dmarc(value)]
            if tracked and not item["custom"] and any((name, rtype, value) not in previous for value in dmarc):
                continue  # the owner's own policy
            work[slot] = [value for value in current if value not in dmarc] + [content]
        elif item["key"] == "dkim":
            work[slot] = [content]
            ttls[slot] = 3600
        elif item["key"] == "webmail":
            if peek(name, "CNAME") or any((name, rtype, value) not in previous and value != content for value in current):
                continue  # the owner points webmail.<domain> elsewhere
            if content not in current:
                current.append(content)
        elif rtype == "CNAME":
            work[slot] = [content]
        elif content not in current:
            current.append(content)
        written.append([name, rtype, content])

    # One refused rrset fails the whole PATCH: leave out whatever would clash
    # with a CNAME at that name (or the other way round).
    present: dict[str, set] = {}
    for rrset in data.get("rrsets", []):
        if rrset["records"]:
            present.setdefault(rrset["name"].rstrip("."), set()).add(rrset["type"])
    for (name, rtype), contents in work.items():
        if contents:
            present.setdefault(name, set()).add(rtype)
        elif rtype in present.get(name, set()):
            present[name].discard(rtype)
    patch, skipped = [], set()
    for (name, rtype), contents in work.items():
        if contents == original[(name, rtype)]:
            continue
        types = present.get(name, set())
        if contents and ((rtype == "CNAME" and types - {"CNAME"}) or (rtype != "CNAME" and "CNAME" in types)):
            log.warning("mail %s record for %s left out: the name has a CNAME", rtype, name)
            skipped.add((name, rtype))
            continue
        patch.append(_replace(name, rtype, contents, ttls.get((name, rtype), 3600)))
    if patch:
        _patch(zone.name, patch)
    _save_managed(db, zone, key, {
        "records": [item for item in written if (item[0], item[1]) not in skipped],
        "spf": spf_asked,
    })


def mail_domain_changed(db: Session, mail_domains) -> None:
    """Keep the mail records of these domains in their zones. Never fails the caller."""
    if not installed():
        return
    for mail_domain in mail_domains:
        try:
            zone = mail_zone(db, mail_domain)
            if zone is not None:
                _write_mail_records(db, zone, mail_domain)
        except Exception as exc:  # noqa: BLE001
            log.warning("mail DNS records for %s were not written: %s", mail_domain.domain, exc)


def mail_domain_removed(db: Session, domain: str, owner_id: int, mail_host: str) -> None:
    """Email is off for a domain: the records the panel wrote for it go, and
    its mechanisms leave the SPF (what the owner added there stays)."""
    if not installed():
        return
    try:
        zone = zone_for_name(db, domain, owner_id)
        if zone is None:
            return
        key = f"mail:{domain}"
        entry = _managed(zone).get(key)
        data = _get_zone(zone.name)
        if not isinstance(entry, dict):
            # Written before the panel kept track: its DKIM key and its MX.
            from app.services.mail import DKIM_SELECTOR

            mx = [v for v in _values(data, domain, "MX") if v.split()[-1].rstrip(".") != mail_host]
            _patch(zone.name, [_replace(f"{DKIM_SELECTOR}._domainkey.{domain}", "TXT", [], 3600),
                               _replace(domain, "MX", mx, (_rrset(data, domain, "MX") or {}).get("ttl") or 3600)])
            return
        slots: dict[tuple[str, str], list[str]] = {}
        for item in entry.get("records", []):
            if len(item) != 3:
                continue
            name, rtype, content = item
            current = slots.setdefault((name, rtype), _values(data, name, rtype))
            if content in current:
                current.remove(content)
        if entry.get("spf"):
            current = slots.setdefault((domain, "TXT"), _values(data, domain, "TXT"))
            spf = [value for value in current if _is_spf(value)]
            if spf:
                ours = {m.lower() for m in _spf_parts(entry["spf"])[0]}
                have, ending = _spf_parts(spf[0])
                left = [m for m in have if m.lower() not in ours]
                slots[(domain, "TXT")] = [value for value in current if value not in spf] + (
                    [_txt(" ".join(["v=spf1"] + left + [ending or "~all"]))] if left else [])
        _patch(zone.name, [_replace(name, rtype, contents, (_rrset(data, name, rtype) or {}).get("ttl") or 3600)
                           for (name, rtype), contents in slots.items()])
        _save_managed(db, zone, key, None)
    except Exception as exc:  # noqa: BLE001
        log.warning("mail DNS records for %s were not removed: %s", domain, exc)


def mail_zone_status(zone: DnsZone, records: list[dict]) -> dict:
    """For the Email page: is each record it lists in the zone here? The same
    test as its public DNS check -- the panel's SPF mechanisms present, any
    DMARC policy unless an administrator set one -- against the zone itself."""
    try:
        data = _get_zone(zone.name)
    except (RuntimeError, LookupError, ValueError):
        data = None
    for record in records:
        name = record["name"].lower().rstrip(".")
        rtype = record["type"].upper()
        if data is None or (name != zone.name and not name.endswith("." + zone.name)):
            record["in_zone"] = None
            continue
        current = _values(data, name, rtype)
        if record["key"] == "spf":
            spf = [value for value in current if _is_spf(value)]
            asked = {m.lower() for m in _spf_parts(record["value"])[0]}
            ok = len(spf) == 1 and asked <= {m.lower() for m in _spf_parts(spf[0])[0]}
        elif record["key"] == "dmarc":
            dmarc = [_untxt(value) for value in current if _is_dmarc(value)]
            want = "".join(record["value"].split()).lower()
            ok = bool(dmarc) and (not record.get("custom") or any("".join(d.split()).lower() == want for d in dmarc))
        else:
            try:
                content = to_content(rtype, record["value"], record.get("priority"), zone.name)
            except ValueError:
                ok = False
            else:
                if rtype == "TXT":
                    ok = _untxt(content) in {_untxt(value) for value in current}
                else:
                    ok = content.lower() in {value.lower() for value in current}
        record["in_zone"] = ok
    return {"id": zone.id, "name": zone.name}


def hosts_zone(db: Session, domain: str) -> Optional[DnsZone]:
    """The zone a wildcard certificate for this domain can be proved in."""
    return zone_for_name(db, (domain or "").lower()) if installed() else None


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def overview(db: Session, actor: User) -> dict:
    is_installed = installed()
    values = current_settings()
    zones = db.query(DnsZone)
    if not _is_admin(actor):
        zones = zones.filter(DnsZone.owner_id == actor.id)
    return {
        "installed": is_installed,
        "is_admin": _is_admin(actor),
        "nameservers": [n for n in (values["ns1"], values["ns2"]) if n],
        "addresses": server_addresses(),
        "zone_count": zones.count(),
        "default_ttl": values["default_ttl"],
        "ttl_choices": list(TTL_CHOICES),
        "record_types": list(RECORD_TYPES),
        "settings": values if _is_admin(actor) else None,
    }


def sync_zones(db: Session, owner_id: Optional[int] = None) -> list[str]:
    """Every domain on the panel -- website and alias names -- has DNS: a
    zone of its own, or its place in a zone above it. Parents first, so a
    subdomain lands in its domain's zone. Websites that came in without the
    panel's website hook (DirectAdmin import, a restore, WHMCS) get theirs
    here: on the minute tick, after the addon is installed, and when the DNS
    page opens. Returns the zones it made."""
    if not installed() or not _ns_records():
        return []
    created = []
    names = _owned_names(db, owner_id)
    for raw, owner in sorted(names.items(), key=lambda item: (item[0].count("."), item[0])):
        name = raw[4:] if raw.startswith("www.") else raw
        if not DOMAIN_RE.fullmatch(name) or zone_for_name(db, name) is not None:
            continue
        owner_user = db.get(User, owner)
        if owner_user is None:
            continue
        try:
            create_zone(db, owner_user, name, owner_id=owner)
            created.append(name)
        except (ValueError, PermissionError, RuntimeError, LookupError) as exc:
            log.warning("zone for %s not created: %s", name, exc)
    return created


def tick() -> None:
    """The panel's minute tick: zones for domains that have none yet."""
    if not installed():
        return
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        created = sync_zones(db)
        if created:
            print(f"opanel DNS Manager made zones for: {', '.join(created)}")
    finally:
        db.close()
