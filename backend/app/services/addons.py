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
# One install or removal at a time. Each is an apt run; two at once had the
# second fail on apt's lock ("Could not get lock /var/lib/apt/lists/lock")
# when an admin installed every addon in a row.
_install_lock = threading.Lock()


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
            "traffic, read, write and delete a website's files, run backups, "
            "issue certificates and switch the WAF; an administrator's can also "
            "block and unblock addresses and add WAF rules. Deleting a file is "
            "marked destructive, so clients ask before running it."
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
    "demo": {
        "id": "demo",
        "name": "Demo mode",
        "summary": "Read-only demo accounts: anyone can look around the panel, nobody can change anything.",
        "description": (
            "Pick one or more accounts and give each a public password. While "
            "Demo mode is on they can open every page their role allows -- an "
            "administrator demo account sees the whole panel -- but every "
            "change is refused on the server, and so are downloads, the web "
            "terminal and phpMyAdmin. The login page can show the demo "
            "accounts with a one-click sign-in, and the panel shows a banner "
            "while a demo account is signed in."
        ),
        "category": "integration",
        "version": "1",
        # Part of the panel: installing it only turns it on. See _is_panel_addon.
        "kind": "panel",
        "packages": [],
        "service": "",
        "features": ["demo"],
        "notes": [
            "Use accounts made for the demo: choosing one resets its password "
            "to the public one, clears its two-factor sign-in, and gives its "
            "SFTP login a random password so the public one opens nothing "
            "but the panel.",
            "With Demo mode stopped or removed a demo account cannot sign in "
            "at all, so it never becomes a full account with a published "
            "password. Taking an account off the list gives it a new random "
            "password.",
            "A demo account sees what its role sees: an administrator demo "
            "account shows other accounts, logs and visitor addresses. Run "
            "the demo on a server holding only sample data.",
        ],
    },
    "mail": {
        "id": "mail",
        "name": "Email",
        "summary": "Mail server with webmail: every hosting account manages its own mailboxes.",
        "description": (
            "Installs Exim, Dovecot and Rspamd, and the BNIX webmail. Hosting "
            "customers turn email on for their own domains and manage their "
            "mailboxes, forwarders and catch-all; each domain gets a DKIM key "
            "and the SPF, DKIM, DMARC and MX records to publish. Rspamd filters "
            "spam (SPF, DKIM, DMARC, DNS blocklists, greylisting, a learning "
            "filter) and sending is rate-limited per mailbox. Webmail opens "
            "from the panel without a password, on port 2096 of the panel "
            "hostname or on webmail.<domain>."
        ),
        "category": "hosting",
        "version": "1",
        "packages": ["exim4-daemon-heavy", "dovecot-imapd", "dovecot-pop3d", "dovecot-lmtpd", "rspamd"],
        "service": "exim4",
        "features": ["mail"],
        "notes": [
            "The panel hostname is the mail server's name: MX records point at "
            "it and mail clients connect to it, so it needs a real certificate. "
            "Set the server's reverse DNS (PTR) to the same name.",
            "Many providers block outgoing port 25. If yours does, set a relay "
            "(smarthost) in the addon's settings or ask the provider to open it.",
            "Opens ports 110, 143, 993, 995 and 2096; 25, 465 and 587 are open "
            "on every install already. Websites' PHP mail() goes through Exim "
            "too, signed with DKIM for the account's own domains.",
            "Expect about 300-400 MB of RAM for Exim, Dovecot, Rspamd and the webmail.",
            "Removing uninstalls the mail server and the webmail. Mailboxes in "
            "/var/vmail, the DKIM keys and the panel's list of mailboxes are "
            "kept, so installing again brings them back.",
        ],
    },
    "dns": {
        "id": "dns",
        "name": "DNS Manager",
        "summary": "This server becomes the nameserver for your customers' domains.",
        "description": (
            "Installs PowerDNS. Hosting customers manage the zones of their own "
            "domains -- A, AAAA, CNAME, MX, TXT, NS, SRV and CAA records -- and "
            "administrators every zone. A new website gets its records "
            "automatically, the Email addon writes MX, SPF, DKIM, DMARC and its "
            "relay's records into the zone, and a wildcard certificate can be "
            "issued over DNS without Cloudflare."
        ),
        "category": "hosting",
        "version": "1",
        "packages": ["pdns-server", "pdns-backend-sqlite3"],
        "service": "pdns",
        "features": ["dns"],
        "notes": [
            "Set the nameservers (default ns1 and ns2 under the panel hostname) "
            "in DNS Manager's settings, then register them with glue records at "
            "the registrar of their domain, pointing at this server's address.",
            "A customer's domain is served from here once its registrar lists "
            "these nameservers.",
            "Opens port 53 (UDP and TCP). Refuses to install next to BIND or dnsmasq.",
            "Removing uninstalls PowerDNS but keeps the zones in /var/lib/opanel-dns, "
            "so installing again brings them back.",
        ],
    },
    "malware": {
        "id": "malware",
        "name": "Malware Scanner",
        "summary": "ClamAV and Linux Malware Detect: scan websites and the server, quarantine threats.",
        "description": (
            "Installs ClamAV with Linux Malware Detect on top of it: LMD's "
            "web-focused signatures catch the PHP shells and injected code "
            "ClamAV misses. Scan one website, every website or the whole "
            "server, now or on a schedule; turn on real-time protection to "
            "scan files as they are written; move a threat to quarantine and "
            "put it back if it was a false positive. clam-juice trims "
            "ClamAV's signatures to what a Linux web server needs, dropping "
            "Windows, macOS and Office malware."
        ),
        "category": "security",
        "version": "1",
        # The scanner keeps its own state -- the panel settings flag and
        # ClamAV's packages -- and its own installer. The addon is the front
        # for them rather than a helper package. See _is_managed.
        "kind": "managed",
        "packages": ["clamav", "clamav-daemon"],
        "service": "clamav-daemon",
        "features": ["malware"],
        "notes": [
            "ClamAV holds its signatures in memory: about 200 MB with "
            "clam-juice's filtered set (1-1.5 GB with the full databases).",
            "Installing downloads ClamAV, its signature database and Linux "
            "Malware Detect, which takes a few minutes.",
            "Stopping turns scanning and real-time protection off and gives "
            "the memory back; the software, schedules, scan history and "
            "quarantine stay.",
            "Removing also uninstalls ClamAV and Linux Malware Detect. Scan "
            "history and quarantined files are kept.",
        ],
    },
    "limits": {
        "id": "limits",
        "name": "Resource limits",
        "summary": "CPU, memory, processes and disk I/O limits for each hosting account.",
        "description": (
            "Gives every hosting account its own share of the server, the way "
            "CloudLinux does but with what Ubuntu's kernel already has (cgroup "
            "v2). Set CPU (100% = one core), memory, the number of processes "
            "and disk read/write speed on a package or an account; one busy "
            "website then slows down or fails on its own instead of taking the "
            "whole server with it. A reseller gets a cap on its whole group "
            "and sets each customer's limits within it. Every account's "
            "current use is shown next to its limits."
        ),
        "category": "hosting",
        "version": "1",
        "packages": [],
        "service": "opanel-limits",
        "features": ["resource_limits"],
        "notes": [
            "Needs cgroup v2: Ubuntu 22.04 or later, on a virtual machine or a "
            "dedicated server. A container (LXC, OpenVZ) cannot run it.",
            "Limits cover the account's PHP, cron jobs, SFTP and the panel's "
            "work on its files. Database queries run inside MariaDB and are "
            "not counted against the account.",
            "0 means unlimited, and every account starts unlimited: nothing "
            "changes until you set a limit.",
            "A process over its memory limit is stopped, which shows as an "
            "error on that website only. Set memory with some room to spare.",
            "Stopping or removing the addon lifts every limit at once.",
        ],
    },
    "notifications": {
        "id": "notifications",
        "name": "Notifications",
        "summary": "Email and Telegram alerts for administrators.",
        "description": (
            "Emails through your SMTP server and messages through your Telegram "
            "bot, for administrators only: failed scheduled backups, stopped "
            "services, a filling disk, certificates that did not renew, "
            "malware, accounts out of storage, panel updates and sign-in "
            "lockouts, plus each administrator's own sign-ins and security "
            "changes. Hosting customers are not notified."
        ),
        "category": "integration",
        "version": "1",
        # Part of the panel: installing it only turns it on. See _is_panel_addon.
        "kind": "panel",
        "packages": [],
        "service": "",
        "features": ["notifications"],
        "notes": [
            "Email needs an SMTP server; many VPS providers block outbound port "
            "25, so use 587 (STARTTLS) or 465 (SSL).",
            "Telegram needs a bot from @BotFather. Server alerts go to the chat "
            "IDs you enter, which can be a group; each administrator can also "
            "link their own chat from the Notifications page.",
            "Messages that cannot be sent are retried for about an hour and "
            "kept in the send log for 30 days.",
        ],
    },
}


def _is_panel_addon(definition: dict) -> bool:
    """An addon that lives inside the panel and needs nothing from root.

    Its installed/running state is the panel's own bookkeeping; there is no
    package to ask the box about, and the helper does not know its id.
    """
    return definition.get("kind") == "panel"


def _is_managed(definition: dict) -> bool:
    """An addon fronting a feature that has its own installer and state.

    The Malware Scanner was a panel feature before it was an addon: whether it
    is on is the panel settings flag, whether it is installed is ClamAV's
    packages. The addon reads and drives those, so a box that already had
    scanning on shows the addon installed without any migration.
    """
    return definition.get("kind") == "managed"


def helper_addon_ids() -> set[str]:
    """The addons the root helper operates; its allowlist must match this."""
    return {key for key, value in ADDONS.items()
            if not _is_panel_addon(value) and not _is_managed(value)}


def is_enabled(addon_id: str) -> bool:
    """Whether an addon without a helper package is installed and switched on."""
    if not is_known(addon_id):
        return False
    if _is_managed(ADDONS[addon_id]):
        from app.services import malware_scan

        return malware_scan._persisted_enabled()
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
    elif _is_managed(definition):
        detail = _malware_status(live, entry)
        entry = {**entry, **live.pop("_busy", {})}
    else:
        detail = _helper_status(addon_id, live)
    return _describe(definition, entry, live, detail)


def _malware_status(live: dict, entry: dict) -> str:
    """The scanner's own answer, in the addon's terms.

    Installed is ClamAV's packages; running is scanning on with clamd up. The
    flag on while the packages are not there yet is an install still under
    way, which the page shows as busy.
    """
    from app.services import malware_scan

    try:
        state = malware_scan.refresh_status()
    except Exception as exc:  # noqa: BLE001 - a status read must not raise
        return str(exc)
    installing = bool(state.get("enabled")) and not state.get("installed")
    live.update(
        installed=bool(state.get("installed")) or installing,
        running=bool(state.get("active")),
        enabled=bool(state.get("installed")),
        version=state.get("lmd_version") or "",
    )
    if installing and not entry.get("busy"):
        live["_busy"] = {"busy": True, "busy_action": "install"}
    if state.get("installed") and not state.get("active"):
        return state.get("detail") or ""
    if installing and "failed" in (state.get("detail") or ""):
        return state.get("detail") or ""
    return ""


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
            down = [name for name in parsed.get("down", "").split(",") if name]
            if down and live["installed"]:
                detail = "Not running: " + ", ".join(down)
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
    if addon_id == "limits" and command in {"addon-install", "addon-enable"}:
        # The agent comes from the panel's own tree, which the panel user can
        # write; the helper installs it only if it matches the hash it ships.
        from app.services import resource_limits

        resource_limits.install_agent()
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
            with _install_lock:
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
            _after_lifecycle(addon_id, action)
        finally:
            _update_state(addon_id, busy=False, busy_action="")

    threading.Thread(target=worker, daemon=True).start()


def _after_lifecycle(addon_id: str, action: str) -> None:
    """Panel-side work once the helper has installed or removed an addon.

    The Email addon's mailboxes live in the panel's database; a fresh install
    gets them written out, with the relay and limits the admin had set.
    """
    if addon_id == "dns" and action == "install":
        # Every domain on the panel has DNS from the start.
        try:
            from app.core.database import SessionLocal
            from app.services import dns_manager

            db = SessionLocal()
            try:
                dns_manager.sync_zones(db)
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001 - record it, never crash the thread
            _update_state(addon_id, last_error=f"Installed, but the zones could not all be made: {exc}")
        return
    if addon_id == "limits" and action == "install":
        try:
            from app.services import resource_limits

            resource_limits.sync_quietly()
        except Exception as exc:  # noqa: BLE001 - record it, never crash the thread
            _update_state(addon_id, last_error=f"Installed, but the accounts could not be added: {exc}")
        return
    if addon_id != "mail":
        return
    try:
        from app.services import mail

        mail.after_lifecycle(action)
    except Exception as exc:  # noqa: BLE001 - record it, never crash the thread
        _update_state(addon_id, last_error=f"Installed, but the mailboxes could not be written out: {exc}")


def _background_call(addon_id: str, action: str, work) -> None:
    """_background for an addon whose work is panel code, not a helper call."""
    def worker() -> None:
        try:
            with _install_lock:
                work()
        except Exception as exc:  # noqa: BLE001 - record it, never crash the thread
            _update_state(addon_id, last_error=str(exc))
        else:
            fields: dict = {"last_error": ""}
            if action == "install":
                fields["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            elif action == "uninstall":
                fields.update(installed_at="", installed_by="")
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
    if _is_managed(definition):
        _update_state(addon_id, busy=True, busy_action="install", last_error="", installed_by=actor)
        _background_call(addon_id, "install", _malware_install)
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
    if _is_managed(definition):
        _malware_stop()
        _update_state(addon_id, busy=True, busy_action="uninstall", last_error="")
        _background_call(addon_id, "uninstall", _malware_remove)
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
    if _is_managed(definition):
        if running:
            _malware_start()
        else:
            _malware_stop()
        return status(addon_id)
    _run_addon_command("addon-enable" if running else "addon-disable", addon_id)
    return status(addon_id)


# ---------------------------------------------------------------------------
# Malware Scanner (managed)
# ---------------------------------------------------------------------------
def _malware_install() -> None:
    """ClamAV (and Linux Malware Detect with it), then scanning on.

    A failed install turns the flag back off, so the addon reads as not
    installed with the error beside it instead of installing forever.
    """
    from app.services import malware_scan, panel_settings

    panel_settings._persist_malware_enabled(True)
    try:
        if not malware_scan.clamav_installed():
            malware_scan.install_clamav()
        malware_scan.start_clamd()
        _wait_for_clamd()
    except Exception:
        panel_settings._persist_malware_enabled(False)
        raise
    finally:
        malware_scan.refresh_status()


def _malware_start() -> None:
    from app.services import malware_scan, panel_settings

    panel_settings.set_malware_scan(True)
    # Always, not only when clamd does not answer: a stop disabled the units,
    # and a clamd that answers anyway would leave them disabled for the next
    # reboot.
    if malware_scan.clamav_installed():
        if not malware_scan.start_clamd():
            raise RuntimeError("Scanning is on, but the ClamAV daemon did not start")
        _wait_for_clamd()


# clamd loads its signatures before it answers -- 10-20 s on a small VPS.
CLAMD_START_WAIT_SECONDS = 60


def _wait_for_clamd() -> None:
    """Return once clamd answers, so Start does not come back reading
    Stopped for the half-minute it spends loading signatures."""
    from app.services import malware_scan

    deadline = time.monotonic() + CLAMD_START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if malware_scan.clamd_running():
            return
        time.sleep(2)


def _malware_stop() -> None:
    """Scanning off and clamd stopped, which is what frees the memory. The
    real-time monitor goes too: it scans through clamd."""
    from app.services import panel_settings

    panel_settings.set_malware_realtime(False)
    panel_settings.set_malware_scan(False)


def _malware_remove() -> None:
    from app.services import malware_scan

    malware_scan.remove_clamav()
    malware_scan.refresh_status()


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
