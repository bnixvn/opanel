"""Optional panel components, installed on demand.

An addon is extra software the panel can put on the box and then operate --
Fail2ban being the first. Two rules shape the whole design, and both come from
the fact that installing one runs as root:

Addons ship with the panel. The registry below is the complete list, and the
root helper carries its own independent copy; nothing outside the repository can
name an addon, and no command text ever crosses the trust boundary. Releasing a
new addon therefore means releasing a new panel version, which is the price of
never handing root to something the repository has not reviewed.

Live truth comes from the box, not from this file. Whether an addon is installed
or running is answered by asking the helper each time. The JSON state here holds
only what the box cannot tell us -- who installed it, when, what the last
failure said, and whether a background install is still running.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from app.services.shell import shell

STATE_FILE = Path("/var/lib/opanel/addons.json")

# An addon id reaches the helper as an argument. Keep the shape narrow enough
# that it cannot be anything but a key, and check it against the registry too --
# the pattern stops a surprise, the registry is the actual authority.
ADDON_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")

_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# The registry: the complete set of addons this panel release knows
# ---------------------------------------------------------------------------
ADDONS: dict[str, dict] = {
    "fail2ban": {
        "id": "fail2ban",
        "name": "Fail2ban",
        "summary": "Bans an address at the firewall after repeated failed logins.",
        "description": (
            "Watches the SSH and panel login logs and drops an address at the "
            "firewall once it has failed too many times. The panel already "
            "rate-limits and locks out a login; Fail2ban stops the traffic one "
            "layer earlier, before it reaches the application at all."
        ),
        "category": "security",
        "version": "1",
        "packages": ["fail2ban"],
        "service": "fail2ban",
        # Surfaces the addon adds to its own page in the panel.
        "features": ["settings", "banned_addresses"],
        "notes": [
            "Bans are inserted above the panel's default port allowances, or "
            "an allow rule for port 22 would let banned traffic straight "
            "through while the ban still looked active.",
            "Addresses in Never ban are never blocked. Your current address is "
            "suggested there so a misconfiguration cannot lock you out.",
        ],
    },
    "mcp": {
        "id": "mcp",
        "name": "MCP server",
        "summary": "Lets AI assistants read and operate the panel through the Model Context Protocol.",
        "description": (
            "Adds an MCP endpoint at /api/mcp that Claude Code, Cursor, VS Code "
            "and other MCP clients can connect to with a personal token. Each "
            "token acts as the account that created it: a customer's token sees "
            "that customer's websites, databases and backups and nothing else. "
            "Tools can list and inspect, read site and access logs, summarise "
            "traffic, run backups, issue certificates and switch the WAF; an "
            "administrator's can also block addresses and add WAF rules. None of "
            "them delete anything."
        ),
        "category": "integration",
        "version": "1",
        # Nothing to install on the box: the endpoint is part of the panel, so
        # installing it only turns it on. See _is_panel_addon.
        "kind": "panel",
        "packages": [],
        "service": "",
        "features": ["mcp_tokens"],
        "notes": [
            "Every user creates their own tokens on the MCP page. A token is "
            "read-only unless it was created with actions allowed.",
            "Stopping the addon turns the endpoint off and keeps the tokens; "
            "removing it also revokes every token.",
            "Clients connect over HTTPS, so the panel needs a certificate they "
            "trust; a self-signed one will be refused.",
        ],
    },
}


def _is_panel_addon(definition: dict) -> bool:
    """An addon that lives inside the panel and needs nothing from root.

    Its installed/running state is the panel's own bookkeeping; there is no
    package to ask the box about, and the helper does not know its id.
    """
    return definition.get("kind") == "panel"


def helper_addon_ids() -> set[str]:
    """The addons the root helper operates; its allowlist must match this."""
    return {key for key, value in ADDONS.items() if not _is_panel_addon(value)}


def is_enabled(addon_id: str) -> bool:
    """Whether a panel addon is installed and switched on."""
    if not is_known(addon_id):
        return False
    entry = _entry(addon_id)
    return bool(entry.get("installed")) and bool(entry.get("running", True))


def registry() -> dict[str, dict]:
    """The addon definitions, without the live state."""
    return {key: dict(value) for key, value in ADDONS.items()}


def is_known(addon_id: str) -> bool:
    return bool(ADDON_ID_RE.match(addon_id or "")) and addon_id in ADDONS


def _require_known(addon_id: str) -> dict:
    if not is_known(addon_id):
        raise ValueError(f"Unknown addon: {addon_id!r}")
    return ADDONS[addon_id]


# ---------------------------------------------------------------------------
# Panel-side state
# ---------------------------------------------------------------------------
def _read_state() -> dict:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _write_state(data: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        # Losing the bookkeeping must never take the Addons page down with it.
        return


def _update_state(addon_id: str, **fields) -> dict:
    with _state_lock:
        data = _read_state()
        entry = dict(data.get(addon_id) or {})
        entry.update(fields)
        data[addon_id] = entry
        _write_state(data)
        return entry


def _entry(addon_id: str) -> dict:
    return dict(_read_state().get(addon_id) or {})


# ---------------------------------------------------------------------------
# Live status, read from the box
# ---------------------------------------------------------------------------
def _parse_kv(text: str) -> dict[str, str]:
    """Parse the helper's `key=value key=value` status line."""
    out: dict[str, str] = {}
    for token in (text or "").split():
        if "=" in token:
            key, _, value = token.partition("=")
            out[key] = value
    return out


def status(addon_id: str) -> dict:
    """What the box says about one addon, merged with our bookkeeping."""
    definition = _require_known(addon_id)
    entry = _entry(addon_id)
    live = {"installed": False, "running": False, "enabled": False, "version": ""}
    detail = ""
    if _is_panel_addon(definition):
        installed = bool(entry.get("installed"))
        running = installed and bool(entry.get("running", True))
        live.update(installed=installed, running=running, enabled=running,
                    version=definition["version"] if installed else "")
    else:
        detail = _helper_status(addon_id, live)
    return _describe(definition, entry, live, detail)


def _helper_status(addon_id: str, live: dict) -> str:
    detail = ""
    try:
        result = shell.privileged("addon-status", helper_args=[addon_id], check=False)
        if result.returncode == 0:
            parsed = _parse_kv(result.stdout)
            live["installed"] = parsed.get("installed") == "1"
            live["running"] = parsed.get("running") == "1"
            live["enabled"] = parsed.get("enabled") == "1"
            live["version"] = parsed.get("version", "")
        else:
            detail = (result.stderr or result.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001 - a status read must not raise
        detail = str(exc)
    return detail


def _describe(definition: dict, entry: dict, live: dict, detail: str) -> dict:
    return {
        "id": definition["id"],
        "kind": definition.get("kind") or "system",
        "name": definition["name"],
        "summary": definition["summary"],
        "description": definition["description"],
        "category": definition["category"],
        "version": definition["version"],
        "features": list(definition.get("features") or []),
        "notes": list(definition.get("notes") or []),
        "service": definition.get("service", ""),
        **live,
        # Bookkeeping the box cannot answer.
        "busy": bool(entry.get("busy")),
        "busy_action": entry.get("busy_action") or "",
        "installed_at": entry.get("installed_at") or "",
        "installed_by": entry.get("installed_by") or "",
        "last_error": entry.get("last_error") or "",
        "detail": detail,
    }


def catalog() -> list[dict]:
    """Every addon this release ships, each with its live status."""
    return [status(addon_id) for addon_id in sorted(ADDONS)]


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------
def _run_addon_command(command: str, addon_id: str) -> str:
    _require_known(addon_id)
    result = shell.privileged(command, helper_args=[addon_id], check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"{command} failed").strip())
    return (result.stdout or "").strip()


def _background(addon_id: str, action: str, command: str) -> None:
    """Run a long helper call in a thread, recording the outcome.

    apt work takes longer than a request should wait, so the endpoint returns
    immediately and the page polls. `busy` is what stops a second install
    landing on top of the first.
    """
    def worker() -> None:
        try:
            _run_addon_command(command, addon_id)
        except Exception as exc:  # noqa: BLE001 - record it, never crash the thread
            _update_state(addon_id, last_error=str(exc))
        else:
            fields: dict = {"last_error": ""}
            if action == "install":
                fields["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            elif action == "uninstall":
                fields["installed_at"] = ""
                fields["installed_by"] = ""
            _update_state(addon_id, **fields)
        finally:
            _update_state(addon_id, busy=False, busy_action="")

    threading.Thread(target=worker, daemon=True).start()


def install(addon_id: str, actor: str = "") -> dict:
    definition = _require_known(addon_id)
    current = status(addon_id)
    if current["busy"]:
        raise ValueError(f"{definition['name']} is already {current['busy_action'] or 'working'}")
    if current["installed"]:
        raise ValueError(f"{definition['name']} is already installed")
    if _is_panel_addon(definition):
        _update_state(addon_id, installed=True, running=True, last_error="", installed_by=actor,
                      installed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return status(addon_id)
    _update_state(addon_id, busy=True, busy_action="install", last_error="", installed_by=actor)
    _background(addon_id, "install", "addon-install")
    return status(addon_id)


def uninstall(addon_id: str, actor: str = "") -> dict:
    definition = _require_known(addon_id)
    current = status(addon_id)
    if current["busy"]:
        raise ValueError(f"{definition['name']} is already {current['busy_action'] or 'working'}")
    if not current["installed"]:
        raise ValueError(f"{definition['name']} is not installed")
    if _is_panel_addon(definition):
        _update_state(addon_id, installed=False, running=False, last_error="",
                      installed_at="", installed_by="")
        return status(addon_id)
    _update_state(addon_id, busy=True, busy_action="uninstall", last_error="")
    _background(addon_id, "uninstall", "addon-uninstall")
    return status(addon_id)


def set_running(addon_id: str, running: bool) -> dict:
    """Start or stop an installed addon's service."""
    definition = _require_known(addon_id)
    current = status(addon_id)
    if not current["installed"]:
        raise ValueError(f"{definition['name']} is not installed")
    if _is_panel_addon(definition):
        _update_state(addon_id, running=bool(running))
        return status(addon_id)
    _run_addon_command("addon-enable" if running else "addon-disable", addon_id)
    return status(addon_id)


# ---------------------------------------------------------------------------
# Fail2ban
# ---------------------------------------------------------------------------
FAIL2BAN_DEFAULTS = {
    "maxretry": 5,
    "bantime": 3600,
    "findtime": 600,
    "jail_sshd": True,
    "jail_panel": True,
    "ignoreip": "",
}

# bantime -1 means "forever" in fail2ban; allow it, but nothing else negative.
_MAX_BANTIME = 365 * 24 * 3600


def _coerce_int(value, field: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a whole number") from exc
    if number < low or number > high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return number


def _valid_ignoreip(text: str) -> str:
    """Space or comma separated addresses/CIDRs, kept as given after checking.

    This lands in a config file read by root, so only characters that can spell
    an address get through -- no shell metacharacters, no newlines.
    """
    raw = (text or "").replace(",", " ").split()
    cleaned: list[str] = []
    for token in raw:
        if not re.fullmatch(r"[0-9a-fA-F:.]+(/\d{1,3})?", token):
            raise ValueError(f"Not an address or CIDR: {token}")
        if len(token) > 49:
            raise ValueError(f"Address is too long: {token}")
        cleaned.append(token)
    if len(cleaned) > 64:
        raise ValueError("At most 64 addresses in Never ban")
    return " ".join(cleaned)


def fail2ban_settings() -> dict:
    entry = _entry("fail2ban")
    stored = entry.get("settings") or {}
    merged = dict(FAIL2BAN_DEFAULTS)
    for key, value in stored.items():
        if key in merged:
            merged[key] = value
    return merged


def fail2ban_save_settings(payload: dict) -> dict:
    """Validate, persist, then hand the values to the helper to write out."""
    current = fail2ban_settings()
    incoming = dict(current)
    if "maxretry" in payload:
        incoming["maxretry"] = _coerce_int(payload["maxretry"], "Max retries", 1, 100)
    if "findtime" in payload:
        incoming["findtime"] = _coerce_int(payload["findtime"], "Find time", 10, _MAX_BANTIME)
    if "bantime" in payload:
        value = payload["bantime"]
        incoming["bantime"] = (
            -1 if str(value).strip() == "-1" else _coerce_int(value, "Ban time", 10, _MAX_BANTIME)
        )
    if "jail_sshd" in payload:
        incoming["jail_sshd"] = bool(payload["jail_sshd"])
    if "jail_panel" in payload:
        incoming["jail_panel"] = bool(payload["jail_panel"])
    if "ignoreip" in payload:
        incoming["ignoreip"] = _valid_ignoreip(payload["ignoreip"])

    if not incoming["jail_sshd"] and not incoming["jail_panel"]:
        raise ValueError("Enable at least one jail, or uninstall the addon instead")

    # The helper reads this on stdin as JSON and writes jail.local itself, so no
    # value is ever interpolated into a command line.
    result = shell.privileged(
        "addon-fail2ban-configure",
        input=json.dumps(incoming),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not apply settings").strip())

    _update_state("fail2ban", settings=incoming)
    return fail2ban_settings()


def fail2ban_banned() -> list[dict]:
    """Currently banned addresses, per jail."""
    result = shell.privileged("addon-fail2ban-banned", check=False)
    if result.returncode != 0:
        return []
    banned: list[dict] = []
    for line in (result.stdout or "").splitlines():
        jail, _, addresses = line.partition(":")
        jail = jail.strip()
        if not jail:
            continue
        for address in addresses.split():
            banned.append({"jail": jail, "address": address})
    return banned


def fail2ban_unban(address: str) -> str:
    """Lift a ban. The address is validated here and again by the helper."""
    token = (address or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F:.]{3,49}", token):
        raise ValueError("Not an address")
    result = shell.privileged("addon-fail2ban-unban", helper_args=[token], check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not unban").strip())
    return (result.stdout or f"{token} unbanned").strip()


def fail2ban_log(lines: int = 40) -> str:
    """Recent fail2ban activity, for the page's detail panel."""
    count = max(1, min(int(lines or 40), 200))
    result = shell.privileged("addon-fail2ban-log", helper_args=[str(count)], check=False)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "").strip()
    return (result.stdout or "").strip()
