"""A site gets the WAF the panel shows it with, from the moment it exists.

openlitespeed.rewrite_vhost defaults to waf_enabled=False, and the paths that
create a site called it without passing it: the vhost had no WAF block while the
database (and so the panel) said the WAF was on, until an update rewrote every
vhost. Installing WordPress on an existing site rewrote its vhost the same way,
dropping a WAF it had - with its certificate, aliases and redirects. Found on a
1.29.1 -> staging update test, 2026-10-04.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.api import maintenance as maintenance_api
from app.api import websites as websites_api
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import HostingPlan, User, Website
from app.services import openlitespeed, provisioning, site_users, storage_quota, waf
from app.services.shell import CommandResult

PASSWORD = "Correct-Horse-Battery-9"


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(User(username="root_admin", email="a@example.test", role="admin", is_active=True,
                hashed_password=hash_password(PASSWORD)))
    db.commit()

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    monkeypatch.setattr(auth_api, "_enforce_rate_limit", lambda key: None)

    # Nothing on the machine: record the WAF file and the vhost instead.
    calls = []
    monkeypatch.setattr(site_users, "ensure_panel_user", lambda *a, **k: None)
    monkeypatch.setattr(site_users, "ensure_site_runtime", lambda domain, root, php, user: user)
    monkeypatch.setattr(site_users, "fix_site_path", lambda *a, **k: None)
    monkeypatch.setattr(websites_api, "_write_placeholder_page", lambda *a, **k: None)
    monkeypatch.setattr(storage_quota, "enforce_user_storage_quota", lambda *a, **k: None)
    monkeypatch.setattr(waf, "sync_website_rules",
                        lambda website, **k: calls.append(("waf-file", website.domain)) or CommandResult("waf", 0, "", ""))
    monkeypatch.setattr(openlitespeed, "rewrite_vhost",
                        lambda domain, root, **kwargs: calls.append(("vhost", domain, kwargs)) or "")
    client = TestClient(app)
    try:
        yield db, client, calls
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _login(client):
    client.cookies.clear()
    assert client.post("/api/auth/login", data={"username": "root_admin", "password": PASSWORD}).status_code == 200


def _post(client, path, body):
    return client.post(path, json=body, headers={"X-CSRF-Token": client.cookies.get("opanel_csrf", "")})


def _vhost_writes(calls, domain):
    return [call[2] for call in calls if call[0] == "vhost" and call[1] == domain]


def test_a_site_created_in_the_panel_has_its_waf_in_the_vhost(env):
    db, client, calls = env
    _login(client)
    response = _post(client, "/api/websites", {"domain": "newsite.test", "app_type": "php", "install_wordpress": False})
    assert response.status_code == 200, response.text

    site = db.query(Website).filter(Website.domain == "newsite.test").one()
    assert site.waf_enabled is True
    writes = _vhost_writes(calls, "newsite.test")
    assert writes and writes[-1]["waf_enabled"] is True
    # The rules file the vhost includes is written first.
    assert calls.index(("waf-file", "newsite.test")) < next(
        i for i, call in enumerate(calls) if call[0] == "vhost")


def test_a_whmcs_account_has_its_waf_in_the_vhost(env, monkeypatch):
    db, _client, calls = env
    monkeypatch.setattr(site_users, "set_panel_user_password", lambda *a, **k: None, raising=False)
    plan = HostingPlan(slug="basic", name="Basic", website_limit=1, storage_limit_mb=1024, app_type="php")
    db.add(plan)
    db.commit()

    account, created = provisioning.create_account(
        db, external_id="whmcs-9", username="whmcsuser", password=PASSWORD, domain="whmcs-site.test",
        package_id=plan.id, php_version="8.4", app_type="php", install_wordpress=False, enable_ssl=False)

    assert created and account.status == "active"
    writes = _vhost_writes(calls, "whmcs-site.test")
    assert writes and writes[-1]["waf_enabled"] is True
    assert ("waf-file", "whmcs-site.test") in calls


def test_installing_wordpress_on_a_site_keeps_its_vhost_whole(env, monkeypatch):
    """The rewrite after the install goes through the one that carries the
    site's WAF, certificate, aliases and redirects."""
    db, client, _calls = env
    admin = db.query(User).filter(User.username == "root_admin").one()
    site = Website(domain="plain.test", owner_id=admin.id, root_path="/home/x/plain.test", linux_user="x",
                   php_version="8.4", app_type="php", status="active", waf_enabled=True)
    db.add(site)
    db.commit()

    rewritten = []
    monkeypatch.setattr(maintenance_api.mariadb, "create_database",
                        lambda domain: {"db_name": "d", "db_user": "u", "db_password": "p"})
    monkeypatch.setattr(maintenance_api.wordpress, "install_wordpress", lambda *a, **k: None)
    monkeypatch.setattr(websites_api, "_rewrite_website_vhost", lambda website, **k: rewritten.append(
        (website.domain, website.app_type, website.nginx_rewrite_mode, k)))

    _login(client)
    response = _post(client, f"/api/maintenance/wordpress/{site.id}/install",
                     {"admin_user": "wpadmin", "admin_password": "Long-Enough-Password-1", "title": "Plain"})
    assert response.status_code == 200, response.text
    assert rewritten == [("plain.test", "wordpress", "front_controller", {})]
