"""Availability, SSO assurance, supply chain, and two small boundary fixes."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.api import auth as auth_api
from app.services import malware_scan, panel_settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# --------------------------------------------------------------------------
# unauthenticated endpoints must not fork sudo + the root helper per request
# --------------------------------------------------------------------------
def test_brand_settings_do_not_fork_the_helper_per_request(monkeypatch):
    """GET /api/health, /favicon.png and /api/panel-settings/public reach this.

    Counted at the expensive layer -- the privileged helper invocation -- not
    at refresh_status, because refresh_status is what owns the memo and
    replacing it would measure the wrong thing.
    """
    privileged_calls = []

    class _Result:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        malware_scan.shell, "privileged",
        lambda *a, **k: (privileged_calls.append(a[0] if a else ""), _Result())[1],
    )
    monkeypatch.setattr(malware_scan, "clamav_installed", lambda: False)
    monkeypatch.setattr(malware_scan, "_socket_path", lambda: "")
    monkeypatch.setattr(malware_scan, "_write_status", lambda state: None)
    malware_scan.invalidate_status_cache()

    for _ in range(25):
        panel_settings.current_settings()

    assert len(privileged_calls) <= 1, (
        f"the root helper was invoked {len(privileged_calls)} times for 25 "
        "anonymous reads; each one costs sudo, bash over a 4,600-line script, "
        "a logger fork and two systemctl D-Bus round trips"
    )


def test_the_cache_expires(monkeypatch):
    calls = []
    monkeypatch.setattr(
        malware_scan, "refresh_status",
        lambda: (calls.append(1), {"enabled": False, "installed": False, "active": False, "detail": ""})[1],
    )
    malware_scan.invalidate_status_cache()
    malware_scan.cached_status()
    malware_scan.cached_status(max_age=0)
    assert len(calls) == 2


def test_a_live_read_refreshes_the_cache(monkeypatch):
    """refresh_status owns the memo, so it cannot go stale behind a change.

    The previous version of this test patched `_collect_status`, which does not
    exist -- monkeypatch(raising=False) silently did nothing, the real
    refresh_status ran, and the assertion held by construction. It tested
    nothing.
    """
    calls = []

    class _Result:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(malware_scan.shell, "privileged",
                        lambda *a, **k: (calls.append(1), _Result())[1])
    monkeypatch.setattr(malware_scan, "clamav_installed", lambda: bool(calls))
    monkeypatch.setattr(malware_scan, "_socket_path", lambda: "")
    monkeypatch.setattr(malware_scan, "_write_status", lambda state: None)

    malware_scan.invalidate_status_cache()
    first = malware_scan.cached_status()
    # Seed a stale value, then prove a live read replaces it rather than
    # sitting beside it.
    malware_scan._STATUS_CACHE = dict(first, detail="stale")
    assert malware_scan.cached_status()["detail"] == "stale"
    fresh = malware_scan.refresh_status()
    assert malware_scan.cached_status() == fresh
    assert malware_scan.cached_status()["detail"] != "stale"


def test_the_cache_read_survives_a_concurrent_invalidate(monkeypatch):
    """`if _STATUS_CACHE is not None: return dict(_STATUS_CACHE)` loaded the
    global twice; an invalidate between those bytecodes raised TypeError out of
    three unauthenticated routes."""
    import app.services.malware_scan as mod

    monkeypatch.setattr(mod, "refresh_status", lambda: {"detail": "rebuilt"})
    mod._STATUS_CACHE = {"detail": "cached"}
    mod._STATUS_CACHE_AT = __import__("time").monotonic()

    real_dict = dict

    def _dict_that_invalidates(value=None, **kw):
        # Simulate the window: the memo is dropped after the None test passes.
        mod._STATUS_CACHE = None
        return real_dict(value) if value is not None else real_dict(**kw)

    monkeypatch.setattr(mod, "dict", _dict_that_invalidates, raising=False)
    # Must not raise, whichever value comes back.
    assert mod.cached_status()["detail"] in {"cached", "rebuilt"}


# --------------------------------------------------------------------------
# SSO
# --------------------------------------------------------------------------
# The two tests that lived here asserted that certain identifiers appeared
# inside a 900-character window of main.py. They passed while the guard they
# described blocked nothing a browser could do -- it was wired to the two
# routes that consume a token and not to GET /sso, the page that feeds them.
# test_sso_flow.py replaces them: it exercises peek vs consume, the click
# requirement, the nonce pairing, the legacy route no longer signing anyone in,
# and the 2FA redirect.


# --------------------------------------------------------------------------
# login rate limiting
# --------------------------------------------------------------------------
# The three tests here asserted that an account-name key fed no counter at all.
# That shape cured the owner-lockout but left distributed guessing against one
# account unmetered, and the long lockout had already been exempt -- so the
# wrong half had been given up. test_login_rate_limit.py replaces them and
# drives the real login() instead: the owner gets in after any number of wrong
# guesses, and those guesses are still throttled across addresses.


# --------------------------------------------------------------------------
# supply chain and export hygiene
# --------------------------------------------------------------------------
def test_installers_resolve_against_the_committed_lockfile():
    for name in ("install.sh", "update.sh"):
        script = (PROJECT_ROOT / "installer" / name).read_text(encoding="utf-8")
        assert "rm -rf node_modules package-lock.json" not in script, (
            f"{name} deletes the lockfile before installing, as root, on the "
            "production host"
        )
        assert "npm ci" in script, f"{name} must resolve against package-lock.json"
    assert (PROJECT_ROOT / "frontend" / "package-lock.json").exists()


def test_update_refuses_a_downgrade():
    script = (PROJECT_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    assert "Refusing to downgrade" in script, (
        "the default channel is a mutable branch applied with git reset --hard, "
        "which accepts a commit older than the installed one"
    )
    assert "ALLOW_DOWNGRADE" in script, "a deliberate rollback needs an escape hatch"
    assert "sort -V" in script


def test_csv_export_neutralizes_spreadsheet_formulas():
    app_jsx = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    body = app_jsx[app_jsx.index("function csvEscape") : app_jsx.index("function formatApiError")]
    assert "=+" in body or "=+\\-@" in body or "^[=+" in body, (
        "path, user_agent and reason are copied verbatim from the access log, "
        "so any internet requester chooses those cells"
    )
    assert "'${text}" in body or "`'${text}`" in body
