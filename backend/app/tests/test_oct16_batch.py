"""The operator's list of 2026-10-16, checked in OPanel as well as BPanel.

- A disk limit of 0 is unlimited (it already was here) and a restore keeps it.
- Panel SSL takes admin@<panel host> over a placeholder like admin@example.com.
- One sign-out ends one session; six hours idle; a refused credential is
  logged with its reason; a page opened while the API restarts waits for it.
- No backup schedule is not a warning.
- OPcache sizes can be set on the PHP page.
"""
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import auth as auth_api
from app.api import deps
from app.core.config import settings
from app.models.entities import User
from app.schemas.schemas import PhpConfigUpdate
from app.services import panel_settings, php, storage_quota

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")


def test_a_disk_limit_of_zero_is_unlimited_and_a_restore_keeps_it():
    assert storage_quota.user_storage_limit_bytes(User(role="end_user", storage_limit_mb=0)) is None
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "backup.py").read_text(encoding="utf-8")
    assert 'int(user_info.get("storage_limit_mb") or 1024)' not in source


@pytest.mark.parametrize("email", ["admin@example.com", "x@sub.example.org", "a@b.invalid", "a@host.local", "a@localhost", ""])
def test_placeholder_addresses_give_way_to_admin_at_the_panel_host(email):
    assert panel_settings.acme_email(email, "", host="panel.example.vn") == "admin@panel.example.vn"


def test_a_real_address_is_used():
    assert panel_settings.acme_email("ops@company.vn", host="panel.example.vn") == "ops@company.vn"


def test_signing_out_ends_this_session_only():
    logout = inspect.getsource(auth_api.logout)
    assert "token_version" not in logout.split('"""')[-1], "a sign-out must not end every other session"
    everywhere = inspect.getsource(auth_api.logout_everywhere)
    assert "current_user.token_version = (current_user.token_version or 0) + 1" in everywhere
    assert "logout({ everywhere: true })" in APP


def test_six_hours_idle():
    assert settings.access_token_expire_minutes == 360


def test_a_refused_credential_says_why(caplog):
    caplog.set_level(logging.WARNING, logger="opanel.auth")
    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"), url=SimpleNamespace(path="/api/websites"))
    deps._log_reject(request, "token-revoked", {"sub": "alice", "iat": 0, "exp": 9e9})
    line = caplog.records[-1].getMessage()
    assert line.startswith("auth-reject reason=token-revoked user=alice ip=203.0.113.9 path=/api/websites")
    assert "age_s=" in line and "exp_in_s=" in line


def test_the_interface_waits_for_an_api_that_is_restarting():
    assert "outcome !== SESSION_UNREACHABLE" in APP
    assert "if (!options?.silent) setError(" in APP, "a background poll says nothing"
    assert "Cannot connect to the" not in APP


def test_no_backup_schedule_is_not_a_warning():
    assert 'tr("No scheduled backup is set up.")' not in APP
    assert "tone: !backups || !backups.schedules ? 'neutral'" in APP


def test_opcache_sizes_replace_the_tuned_lines(monkeypatch, tmp_path):
    ini = tmp_path / "99-opanel.ini"
    ini.write_text("opcache.memory_consumption = 256\nopcache.revalidate_freq = 60\n", encoding="utf-8")
    monkeypatch.setattr(php, "_lsphp_opanel_ini_path", lambda version: ini)
    monkeypatch.setattr(settings, "command_dry_run", True)
    content = php.update_php_ini(PhpConfigUpdate(php_version="8.3", opcache_memory_consumption=768,
                                                 opcache_interned_strings_buffer=64,
                                                 opcache_max_accelerated_files=100000))
    assert "opcache.memory_consumption = 768" in content and "= 256" not in content
    assert "opcache.revalidate_freq = 60" in content, "the rest of the tuning stays"
    kept = php.update_php_ini(PhpConfigUpdate(php_version="8.3"))
    assert "opcache.memory_consumption = 256" in kept, "empty keeps the tuned value"


def test_the_interned_strings_must_fit():
    with pytest.raises(ValueError):
        PhpConfigUpdate(opcache_memory_consumption=64, opcache_interned_strings_buffer=64)


def test_the_page_is_asked_for_again_every_time(monkeypatch, tmp_path):
    """A tab brought back after sleep was drawn from the cache and showed the
    sign-in form with nothing wrong on the server."""
    from fastapi.testclient import TestClient

    from app import main

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>panel</title>", encoding="utf-8")
    (tmp_path / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    monkeypatch.setattr(main, "frontend_dist", tmp_path)
    client = TestClient(main.app)
    for path in ("/", "/websites", "/index.html"):
        response = client.get(path)
        assert response.status_code == 200 and "panel" in response.text
        assert response.headers.get("cache-control") == "no-cache", path
    assert "no-cache" not in client.get("/robots.txt").headers.get("cache-control", "")
