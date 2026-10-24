"""The Shared blocklist addon: report attackers, block what other servers saw.

With the addon on, this server
- registers with the BNIX collector (blocklist.bnix.io.vn) and keeps the key
  it is given; its reports count once the operator approves it there;
- every few minutes sends the addresses its Fail2ban jails banned and its WAF
  denied on rules that only an attack trips (php2shell, wp2shell, probes for
  .env, .git, keys and webshells), never SQL/XSS heuristics or bad bots;
- reads the shared list - addresses that at least two approved servers
  reported within a week - through the firewall's URL blocklists, hourly.

Everything is reconciled from the minute tick (backup_scheduler), so turning
the addon on or off, a lost state file or a collector that was down all heal
on their own: on means registered, subscribed and reporting; off means none
of it is left behind.
"""
import ipaddress
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.core.version import APP_VERSION
from app.services.shell import shell

logger = logging.getLogger("opanel.shared_blocklist")

ADDON_ID = "sharedblocklist"
PANEL = "opanel"
COLLECTOR = os.environ.get("opanel_SHARED_BLOCKLIST_URL", "https://blocklist.bnix.io.vn").rstrip("/")
LIST_URL = f"{COLLECTOR}/v1/blocklist.txt"
STATE_FILE = Path(os.environ.get("opanel_SHARED_BLOCKLIST_STATE", "/var/lib/opanel/shared-blocklist.json"))

REPORT_EVERY = 300
STATUS_EVERY_PENDING = 600
STATUS_EVERY_APPROVED = 6 * 3600
REFRESH_EVERY = 3600
REGISTER_RETRY = 1800
# A first report looks back this far, not at everything Fail2ban ever banned.
FIRST_LOOKBACK = 24 * 3600
BATCH = 500
TIMEOUT = 20

_lock = threading.Lock()


# ----- state ----------------------------------------------------------------

def _read() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    # The key is a credential: readable by the panel only.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(tmp, STATE_FILE)


def _update(**fields) -> dict:
    state = _read()
    state.update(fields)
    _write(state)
    return state


def enabled() -> bool:
    from app.services import addons

    return addons.is_enabled(ADDON_ID)


# ----- the collector --------------------------------------------------------

def _call(method: str, path: str, body=None, key: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json", "User-Agent": f"{PANEL}/{APP_VERSION}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(f"{COLLECTOR}{path}", method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}")
        except ValueError:
            detail = {}
        return exc.code, detail


def _register(state: dict) -> dict:
    if time.time() - float(state.get("register_tried_at") or 0) < REGISTER_RETRY and state.get("last_error"):
        return state
    state = _update(register_tried_at=time.time())
    try:
        status, answer = _call("POST", "/v1/register", {
            "hostname": socket.gethostname()[:120], "panel": PANEL, "version": APP_VERSION})
    except (OSError, ValueError) as exc:
        return _update(last_error=f"Could not reach {COLLECTOR}: {exc}")
    if status != 201 or not answer.get("key"):
        return _update(last_error=f"Registration refused ({status}): {answer.get('detail', '')}")
    return _update(key=answer["key"], reporter_id=answer.get("id"), status=answer.get("status", "pending"),
                   registered_at=time.time(), status_checked_at=time.time(), last_error="",
                   cursor=int(time.time()) - FIRST_LOOKBACK)


def _check_status(state: dict, force: bool = False) -> dict:
    every = STATUS_EVERY_APPROVED if state.get("status") == "approved" else STATUS_EVERY_PENDING
    if not force and time.time() - float(state.get("status_checked_at") or 0) < every:
        return state
    try:
        status, answer = _call("GET", "/v1/me", key=state["key"])
    except (OSError, ValueError) as exc:
        return _update(status_checked_at=time.time(), last_error=f"Could not reach {COLLECTOR}: {exc}")
    if status == 401:
        # Revoked, or the collector lost it: start again with a new key.
        return _update(key="", reporter_id=None, status="", status_checked_at=time.time(),
                       last_error="The collector no longer knows this server's key; registering again.")
    if status != 200:
        return _update(status_checked_at=time.time(), last_error=f"Status check failed ({status})")
    return _update(status=answer.get("status", ""), status_checked_at=time.time(), last_error="")


# ----- what this server saw -------------------------------------------------

def events(since: int) -> list[dict]:
    """Bans and WAF denials after `since`, from the helper (root reads the logs)."""
    result = shell.privileged("shared-blocklist-events", helper_args=[str(int(since))], check=False,
                              fallback=["bash", "-lc", "true"])
    rows = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        at, source, ip = parts
        try:
            if not ipaddress.ip_address(ip).is_global:
                continue
            rows.append({"at": int(at), "source": source[:60], "ip": ip})
        except ValueError:
            continue
    return rows


def _report(state: dict) -> dict:
    if time.time() - float(state.get("reported_at") or 0) < REPORT_EVERY:
        return state
    cursor = int(state.get("cursor") or (time.time() - FIRST_LOOKBACK))
    found = events(cursor)
    # One report per address and source; the newest time moves the cursor.
    unique = {}
    for row in found:
        unique[(row["ip"], row["source"])] = row
    items = list(unique.values())
    sent = 0
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        try:
            status, answer = _call("POST", "/v1/report", {"items": [
                {"ip": row["ip"], "source": row["source"]} for row in batch]}, key=state["key"])
        except (OSError, ValueError) as exc:
            return _update(reported_at=time.time(), last_error=f"Could not reach {COLLECTOR}: {exc}")
        if status == 403:
            return _update(reported_at=time.time(), status="pending")
        if status == 401:
            return _update(key="", status="", reported_at=time.time(),
                           last_error="The collector no longer knows this server's key; registering again.")
        if status != 200:
            return _update(reported_at=time.time(), last_error=f"Report refused ({status}): {answer.get('detail', '')}")
        sent += len(batch)
    newest = max([row["at"] for row in found], default=cursor)
    return _update(reported_at=time.time(), cursor=max(cursor, newest), last_error="",
                   last_sent=sent, sent_total=int(state.get("sent_total") or 0) + sent,
                   last_report_ok_at=time.time() if sent else state.get("last_report_ok_at"))


# ----- reading the shared list ----------------------------------------------

def _subscribed() -> bool:
    """Whether the firewall reads the list (its URL file belongs to root)."""
    from app.services import firewall

    try:
        return LIST_URL in (firewall.blocklists().stdout or "").split()
    except Exception:  # noqa: BLE001 - "not known" reads as not subscribed
        return False


def _subscribe(state: dict) -> dict:
    """Subscribed, and the list read again once an hour (the firewall's own
    timer reads its lists once a day)."""
    from app.services import firewall

    if state.get("subscribed") and time.time() - float(state.get("refreshed_at") or 0) < REFRESH_EVERY:
        return state
    if not _subscribed():
        result = firewall.add_blocklist_url(LIST_URL)
        if result.returncode != 0:
            return _update(last_error=f"Could not add the list to the firewall: {(result.stderr or result.stdout).strip()[:300]}")
    result = firewall.update_blocklists()
    return _update(subscribed=True, refreshed_at=time.time(),
                   refresh_error="" if result.returncode == 0 else (result.stderr or result.stdout).strip()[:300])


def _waf_audit(on: bool) -> None:
    shell.privileged("shared-blocklist-waf-audit", helper_args=["on" if on else "off"], check=False,
                     fallback=["bash", "-lc", "true"])


# ----- the tick -------------------------------------------------------------

def reconcile() -> dict:
    """Make the server match the addon's switch. Safe to run at any time."""
    with _lock:
        state = _read()
        if not enabled():
            if state:
                from app.services import firewall

                if _subscribed():
                    firewall.delete_blocklist_url(LIST_URL)
                    firewall.update_blocklists()
                _waf_audit(False)
                # Deleted means gone: the key goes with the addon.
                try:
                    STATE_FILE.unlink()
                except OSError:
                    pass
            return {}
        if not state.get("waf_audit"):
            _waf_audit(True)
            state = _update(waf_audit=True)
        if not state.get("key"):
            state = _register(state)
            if not state.get("key"):
                return state
        state = _check_status(state)
        if state.get("status") == "approved":
            state = _report(state)
        return _subscribe(state)


def tick() -> None:
    try:
        reconcile()
    except Exception as exc:  # noqa: BLE001 - the minute tick must go on
        logger.warning("shared blocklist: %s", exc)


def reconcile_in_background() -> None:
    threading.Thread(target=tick, daemon=True).start()


def status() -> dict:
    state = _read()
    return {
        "enabled": enabled(),
        "collector": COLLECTOR,
        "list_url": LIST_URL,
        "registered": bool(state.get("key")),
        "reporter_id": state.get("reporter_id"),
        "status": state.get("status", ""),
        "subscribed": _subscribed(),
        "last_error": state.get("last_error", ""),
        "refresh_error": state.get("refresh_error", ""),
        "registered_at": state.get("registered_at"),
        "reported_at": state.get("last_report_ok_at"),
        "refreshed_at": state.get("refreshed_at"),
        "last_sent": state.get("last_sent", 0),
        "sent_total": state.get("sent_total", 0),
    }


def check_now() -> dict:
    """The Check button: ask the collector now instead of at the next interval."""
    with _lock:
        state = _read()
        if enabled() and state.get("key"):
            _check_status(state, force=True)
    return status()
