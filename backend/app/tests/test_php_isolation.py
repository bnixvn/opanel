"""open_basedir: off by default, per site, and moved with the site's package.

It turns PHP's realpath cache off, about a fifth of an uncached WordPress page
on .41 (2026-10-23). The operator's rule (2026-10-22): off by default; a host
turns it on in a package, and turning it on or off in a package moves every
site of that package's accounts.

The same change gives every site's PHP a root-owned settings directory of its
user's (PHP_INI_SCAN_DIR) with OPcache's file cache: the first request after
an idle spell went from 1.7 s to 0.5 s.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.entities import HostingPlan, User, Website
from app.services import openlitespeed, php_isolation


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    rewrites = []
    import app.api.websites as websites_api
    monkeypatch.setattr(websites_api, "_rewrite_website_vhost",
                        lambda website, **kw: rewrites.append((website.domain, kw.get("open_basedir"))))
    session.rewrites = rewrites
    yield session
    session.close()


def _account(db, name, plan=None):
    user = User(username=name, email=f"{name}@example.test", hashed_password="x", role="end_user",
                plan_id=plan.id if plan else None)
    db.add(user)
    db.commit()
    for n in (1, 2):
        db.add(Website(domain=f"{name}{n}.test", owner_id=user.id, root_path=f"/home/{name}/{name}{n}.test",
                       linux_user=name, app_type="wordpress"))
    db.commit()
    return user


def test_a_new_site_follows_its_package_and_is_off_without_one(db):
    plan = HostingPlan(slug="secure", name="Secure", open_basedir=True)
    db.add(plan)
    db.commit()
    on_plan = _account(db, "alice", plan)
    no_plan = _account(db, "bob")
    assert php_isolation.default_for(db, on_plan) is True
    assert php_isolation.default_for(db, no_plan) is False
    assert Website().open_basedir in (False, None)


def test_turning_it_on_for_a_package_moves_every_site_of_its_accounts(db):
    plan = HostingPlan(slug="basic", name="Basic", open_basedir=False)
    db.add(plan)
    db.commit()
    _account(db, "carol", plan)
    _account(db, "dave")  # not on the package: untouched
    plan.open_basedir = True
    db.commit()
    assert php_isolation.sync_plan(db, plan) == []
    on = {w.domain for w in db.query(Website).filter(Website.open_basedir.is_(True))}
    assert on == {"carol1.test", "carol2.test"}
    assert sorted(db.rewrites) == [("carol1.test", True), ("carol2.test", True)]


def test_a_site_whose_vhost_cannot_be_rewritten_keeps_its_old_value(db, monkeypatch):
    plan = HostingPlan(slug="p", name="P", open_basedir=True)
    db.add(plan)
    db.commit()
    _account(db, "erin", plan)
    import app.api.websites as websites_api

    def flaky(website, **kw):
        if website.domain == "erin2.test":
            raise RuntimeError("helper failed")

    monkeypatch.setattr(websites_api, "_rewrite_website_vhost", flaky)
    assert php_isolation.sync_plan(db, plan) == ["erin2.test"]
    flags = {w.domain: bool(w.open_basedir) for w in db.query(Website)}
    assert flags == {"erin1.test": True, "erin2.test": False}


def _render(**kw):
    return openlitespeed.render_vhost("site.test", "/home/sam/site.test", app_type="wordpress",
                                      php_version="8.4", linux_user="sam", **kw)


def test_the_vhost_carries_open_basedir_only_when_asked(monkeypatch):
    monkeypatch.setattr(openlitespeed, "_php_workers", lambda domain: 4)
    assert "open_basedir" not in _render()
    assert "php_admin_value   open_basedir /home/sam/site.test" in _render(open_basedir=True)


def test_the_site_php_reads_its_users_settings_directory(monkeypatch):
    monkeypatch.setattr(openlitespeed, "_php_workers", lambda domain: 4)
    assert "env                   PHP_INI_SCAN_DIR=:/etc/opanel/php-users/sam" in _render()
