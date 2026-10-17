"""LiteSpeed's page cache per site (as SNPanel's, 2026-10-17).

Each PHP or WordPress vhost turns the lookup on or off and keeps what it
stores in a folder of its own, so "Clear cache" empties that site alone.
"""
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
from app.models.entities import User, Website
from app.services import openlitespeed

HELPER = open(__file__.replace("backend\\app\\tests\\test_website_lscache.py", "installer\\files\\opanel-helper.sh")
              .replace("backend/app/tests/test_website_lscache.py", "installer/files/opanel-helper.sh"), encoding="utf-8").read()


def _render(**kw):
    return openlitespeed.render_vhost("shop.example", "/home/acme/shop.example", app_type=kw.pop("app_type", "wordpress"),
                                      php_version="8.3", linux_user="acme", **kw)


def test_on_it_stores_in_the_sites_own_folder():
    text = _render(lscache=True)
    assert "module cache {" in text and "ls_enabled            1" in text
    assert "storagePath /usr/local/lsws/cachedata/opanel-sites/shop.example" in text


def test_off_the_vhost_turns_the_lookup_off():
    text = _render(lscache=False)
    assert "ls_enabled            0" in text and "storagePath" not in text


def test_a_static_site_has_no_cache_block():
    assert "module cache" not in _render(app_type="static")


def test_re_reading_a_vhost_keeps_its_cache_setting(monkeypatch):
    seen = {}
    monkeypatch.setattr(openlitespeed, "rewrite_vhost", lambda domain, root, **kw: seen.update(kw) or "")
    for setting in (True, False):
        monkeypatch.setattr(openlitespeed, "read_vhost_config", lambda domain, s=setting: _render(lscache=s))
        openlitespeed._rewrite_existing_vhost("shop.example")
        assert seen["lscache"] is setting
    # A vhost written before the switch had it on.
    monkeypatch.setattr(openlitespeed, "read_vhost_config",
                        lambda domain: _render(lscache=True).replace("# OPANEL LSCACHE BEGIN", "").replace("ls_enabled", "x"))
    openlitespeed._rewrite_existing_vhost("shop.example")
    assert seen["lscache"] is True


def test_the_helper_empties_one_sites_folder_only():
    body = HELPER.split("  lscache-purge)", 1)[1].split(";;", 1)[0]
    assert 'require_domain "$1"' in body
    assert 'cache_dir="/usr/local/lsws/cachedata/opanel-sites/$1"' in body
    assert "-L \"$cache_dir\"" in body  # never follows a link out
    delete = HELPER.split("  ols-vhost-delete)", 1)[1].split(";;", 1)[0]
    assert 'rm -rf "/usr/local/lsws/cachedata/opanel-sites/${safe_domain}"' in delete


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    owner = User(username="acme", email="acme@example.test", role="end_user", is_active=True,
                 hashed_password=hash_password("PasswordLongEnough1"))
    db.add(owner)
    db.commit()
    site = Website(domain="shop.example", owner_id=owner.id, root_path="/home/acme/shop.example", app_type="wordpress",
                   php_version="8.3", linux_user="acme", status="active")
    static = Website(domain="plain.example", owner_id=owner.id, root_path="/home/acme/plain.example", app_type="static",
                     linux_user="acme", status="active")
    db.add_all([site, static])
    db.commit()
    rendered, purged = [], []
    monkeypatch.setattr(websites_api, "_rewrite_website_vhost", lambda website, **kw: rendered.append(kw) or "")
    monkeypatch.setattr(websites_api.openlitespeed, "purge_site_cache", lambda domain: purged.append(domain) or 3)

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        yield SimpleNamespace(db=db, client=TestClient(app), site=site, static=static, rendered=rendered, purged=purged)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_the_owner_turns_it_off_and_the_cache_is_emptied(env):
    answer = env.client.put(f"/api/websites/{env.site.id}/lscache", json={"on": False})
    assert answer.status_code == 200, answer.text
    assert answer.json()["lscache_enabled"] is False
    assert env.rendered == [{"lscache": False}] and env.purged == ["shop.example"]
    env.purged.clear()
    # The same setting again changes nothing stored.
    env.client.put(f"/api/websites/{env.site.id}/lscache", json={"on": False})
    assert env.purged == []


def test_clear_cache_answers_how_many_pages_went(env):
    answer = env.client.post(f"/api/websites/{env.site.id}/lscache/purge")
    assert answer.status_code == 200 and answer.json()["files"] == 3


def test_not_for_a_static_site(env):
    assert env.client.put(f"/api/websites/{env.static.id}/lscache", json={"on": True}).status_code == 400
