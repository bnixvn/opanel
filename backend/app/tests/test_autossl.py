"""AutoSSL: a certificate for every website whose names point here (as SNPanel's, 2026-10-17)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import websites as websites_api
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import AutosslSite, User, Website, WebsiteAlias
from app.services import autossl, panel_settings

OURS = {"203.0.113.10"}


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    boss = User(username="boss", email="boss@example.test", role="admin", is_active=True,
                hashed_password=hash_password("PasswordLongEnough1"))
    db.add(boss)
    db.commit()
    site = Website(domain="shop.example", owner_id=boss.id, root_path="/home/boss/shop.example", app_type="php",
                   php_version="8.3", linux_user="boss", status="active", ssl_enabled=False, ssl_mode="none")
    db.add(site)
    db.commit()
    db.add(WebsiteAlias(website_id=site.id, domain="www.shop.example", mode="alias"))
    db.commit()
    store = {"autossl": {"enabled": True}}
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: dict(store))
    monkeypatch.setattr(panel_settings, "_write_raw", lambda data: store.update(data))
    monkeypatch.setattr(autossl, "server_ips", lambda: set(OURS))
    dns = {"shop.example": ["203.0.113.10"], "www.shop.example": ["203.0.113.10"]}
    monkeypatch.setattr(autossl, "_resolve", lambda name: (dns.get(name, []), ""))
    issued = []

    def issue(db_, website, names):
        if issued and issued[-1] == "fail":
            raise websites_api.LetsEncryptError("Challenge failed")
        issued.append(list(names))
        website.ssl_enabled, website.ssl_mode = True, "letsencrypt"
        db_.commit()

    monkeypatch.setattr(websites_api, "issue_letsencrypt", issue)
    monkeypatch.setattr(autossl, "current_certificate", lambda website: None)

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: boss
    try:
        yield SimpleNamespace(db=db, site=site, dns=dns, issued=issued, store=store, client=TestClient(app))
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_a_name_counts_only_when_all_its_addresses_are_ours(monkeypatch):
    answers = {"here.example": ["203.0.113.10"], "split.example": ["203.0.113.10", "198.51.100.5"], "gone.example": []}
    monkeypatch.setattr(autossl, "_resolve", lambda name: (answers[name], ""))
    assert autossl.check_name("here.example", OURS)["ok"] is True
    assert autossl.check_name("split.example", OURS)["ok"] is False
    assert autossl.check_name("gone.example", OURS)["reason"] == "The name does not resolve on public DNS"


def test_names_pointing_here_get_a_certificate(env):
    result = autossl.run_site(env.db, env.site)
    assert result["status"] == "secured" and result.get("issued") is True
    assert env.issued == [["shop.example", "www.shop.example"]]


def test_only_the_names_that_point_here_are_asked_for(env):
    env.dns["www.shop.example"] = ["198.51.100.5"]
    result = autossl.run_site(env.db, env.site)
    assert env.issued == [["shop.example"]]
    www = next(item for item in result["names"] if item["name"] == "www.shop.example")
    assert www["ok"] is False and "198.51.100.5" in www["reason"]


def test_the_sites_own_name_elsewhere_waits_and_backs_off(env):
    env.dns["shop.example"] = ["198.51.100.5", "203.0.113.10"]
    first = autossl.run_site(env.db, env.site)
    assert first["status"] == "waiting" and env.issued == []
    row = env.db.get(AutosslSite, env.site.id)
    assert row.failures == 1 and row.next_check - row.last_check == timedelta(hours=1)
    autossl.run_site(env.db, env.site)
    autossl.run_site(env.db, env.site)
    env.db.refresh(row)
    assert row.next_check - row.last_check == timedelta(hours=4)


def test_a_certificate_of_the_owners_choosing_is_left_alone(env):
    env.site.ssl_mode, env.site.ssl_enabled = "manual", True
    env.db.commit()
    assert autossl.run_site(env.db, env.site)["status"] == "skipped" and env.issued == []


def test_the_pass_does_nothing_while_off_and_respects_an_opt_out(env):
    env.store["autossl"] = {"enabled": False}
    assert autossl.tick(env.db) == {"checked": 0, "issued": 0, "failed": 0}
    env.store["autossl"] = {"enabled": True}
    answer = env.client.patch(f"/api/websites/{env.site.id}/autossl", json={"enabled": False})
    assert answer.status_code == 200 and answer.json()["enabled"] is False
    env.db.expire_all()
    assert autossl.tick(env.db)["checked"] == 0
    env.client.patch(f"/api/websites/{env.site.id}/autossl", json={"enabled": True})
    env.db.expire_all()
    assert autossl.tick(env.db)["issued"] == 1


def test_settings_check_the_extra_addresses(env):
    assert env.client.put("/api/autossl/settings", json={"extra_ips": "198.51.100.7 2001:db8::1"}).json()["extra_ips"] \
        == ["198.51.100.7", "2001:db8::1"]
    assert env.client.put("/api/autossl/settings", json={"extra_ips": ["not-an-ip"]}).status_code == 400
