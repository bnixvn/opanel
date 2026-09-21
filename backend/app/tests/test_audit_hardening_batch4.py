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
    """refresh_status owns the memo, so the cache cannot go stale behind a
    state change: every panel_settings path that toggles the scanner re-reads
    the status through refresh_status, which updates the memo as it goes.
    That is why no invalidate call is sprinkled through those functions."""
    values = iter([
        {"enabled": False, "installed": False, "active": False, "detail": "a"},
        {"enabled": True, "installed": True, "active": True, "detail": "b"},
    ])
    monkeypatch.setattr(malware_scan, "_write_status", lambda state: None)
    monkeypatch.setattr(malware_scan, "_collect_status", lambda: next(values), raising=False)

    malware_scan.invalidate_status_cache()
    monkeypatch.setattr(malware_scan, "refresh_status", malware_scan.refresh_status)
    # Seed the memo, then prove a direct refresh replaces it rather than
    # leaving cached_status serving the old value.
    malware_scan._STATUS_CACHE = {"detail": "stale"}
    malware_scan._STATUS_CACHE_AT = __import__("time").monotonic()
    assert malware_scan.cached_status()["detail"] == "stale"
    fresh = malware_scan.refresh_status()
    assert malware_scan.cached_status() == fresh, (
        "a live read must replace the memo, not sit beside it"
    )


# --------------------------------------------------------------------------
# SSO must not be a lower-assurance parallel door
# --------------------------------------------------------------------------
def test_sso_refuses_an_account_with_two_factor_enabled():
    source = _code(Path(PROJECT_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8"))
    helper = source[source.index("def _sso_user_or_401") : source.index("@app.post(\"/sso\"")]
    assert "totp_enabled" in helper, (
        "the password path enforces totp_enabled and impersonation re-prompts; "
        "SSO went straight from consume_sso_token to _issue_login_session"
    )
    assert "403" in helper


def test_both_sso_routes_go_through_the_guard():
    source = _code(Path(PROJECT_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8"))
    for route in ('@app.post("/sso"', '@app.get("/sso/{token}"'):
        block = source[source.index(route) : source.index(route) + 900]
        assert "_sso_user_or_401" in block, f"{route} must use the shared guard"
        assert "_reject_cross_site_sso" in block, f"{route} needs cross-site binding"
        assert "consume_sso_token(db, token)" not in block, (
            f"{route} must not re-implement the handshake"
        )


# --------------------------------------------------------------------------
# a username key must not be able to deny its own account
# --------------------------------------------------------------------------
def test_an_account_name_key_feeds_no_rate_limit_counter():
    for name in ("_memory_record_failure", "_redis_record_failure"):
        body = _code(inspect.getsource(getattr(auth_api, name)))
        guard = body.index("if not apply_lockout:")
        # The early return has to come before any counter is touched.
        for counter in ("_login_attempts[", "attempts_key = ", "zadd", "attempts.append"):
            if counter in body:
                assert body.index(counter) > guard, (
                    f"{name} records into {counter!r} before exempting account-name keys; "
                    "that is the account-DoS the lockout exemption exists to prevent"
                )


def test_wrong_passwords_for_one_account_do_not_lock_out_its_owner(monkeypatch):
    monkeypatch.setattr(auth_api, "_rate_limit_backend", lambda: "memory")
    auth_api._login_attempts.clear()
    auth_api._login_failures.clear()
    auth_api._login_lockouts.clear()

    user_key = auth_api._username_key("victim")
    for _ in range(auth_api._LOGIN_MAX_ATTEMPTS * 3):
        auth_api._record_failure(user_key, apply_lockout=False)

    # The real owner must still be able to reach the login form.
    auth_api._enforce_rate_limit(user_key)


def test_source_rate_limiting_still_applies(monkeypatch):
    monkeypatch.setattr(auth_api, "_rate_limit_backend", lambda: "memory")
    auth_api._login_attempts.clear()
    auth_api._login_failures.clear()
    auth_api._login_lockouts.clear()

    ip_key = auth_api._client_key.__wrapped__ if hasattr(auth_api._client_key, "__wrapped__") else None
    key = "ip:203.0.113.7"
    for _ in range(auth_api._LOGIN_MAX_ATTEMPTS + 2):
        auth_api._record_failure(key, apply_lockout=True)
    with pytest.raises(Exception):
        auth_api._enforce_rate_limit(key)


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
