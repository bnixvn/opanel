"""A cPanel import puts the account on the package of its cPanel plan, made
from the plan's limits when the panel has none of that name, and switches its
object cache on (operator, 2026-11-08, as SNPanel v2.5.1)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.entities import HostingPlan, User
from app.services import cpanel_import, php_isolation, redis_user


@pytest.mark.parametrize("plan, expected", [
    (" Gold  Plan\t", "Gold Plan"), ("default", None), ("Undefined", None), ("", None), ("x" * 150, "x" * 100),
])
def test_package_name(plan, expected):
    assert cpanel_import.package_name(plan) == expected


def test_plan_limits_follow_the_plan():
    values, notes = cpanel_import.plan_limits(
        {"MAXADDON": "3", "MAXSQL": "unlimited", "MAXPOP": "10", "MAXSUB": "5", "BWLIMIT": "1000"}, "2048")
    assert values == {"website_limit": 4, "storage_limit_mb": 2048, "database_limit": 0, "mailbox_limit": 10}
    assert "BWLIMIT=1000" in notes[0] and "MAXSUB=5" in notes[0]
    values, notes = cpanel_import.plan_limits({"MAXADDON": "unlimited", "DISK_BLOCK_LIMIT": "5242880", "HASSHELL": "1"}, None)
    assert values["website_limit"] == cpanel_import.WEBSITES_MAX and values["storage_limit_mb"] == 5120
    assert any("Terminal off" in note for note in notes)
    assert cpanel_import.plan_limits({}, "unlimited")[0]["storage_limit_mb"] == 0


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(username="shop", email="shop@example.test", role="end_user", is_active=True,
                     hashed_password="x", token_version=0))
    session.commit()
    monkeypatch.setattr(php_isolation, "sync_user", lambda db, user: [])
    yield session
    session.close()


def _account(plan="Gold", **cp):
    return {"plan": plan, "cp": {"MAXADDON": "2", "MAXSQL": "5", "MAXPOP": "20", **cp}, "quota": "3000"}


def test_a_new_package_is_made_and_the_account_put_on_it(db):
    summary = {"username": "shop"}
    cpanel_import._apply_package(db, _account(), summary)
    plan = db.query(HostingPlan).filter(HostingPlan.name == "Gold").one()
    assert (plan.website_limit, plan.storage_limit_mb, plan.database_limit, plan.mailbox_limit) == (3, 3000, 5, 20)
    assert plan.owner_id is None and plan.terminal_enabled is False
    user = db.query(User).filter(User.username == "shop").one()
    assert user.plan_id == plan.id and user.website_limit == 3 and summary["package"] == "Gold"


def test_an_existing_package_is_used_as_it_is(db):
    db.add(HostingPlan(slug="gold", name="GOLD", website_limit=50, storage_limit_mb=99))
    db.commit()
    cpanel_import._apply_package(db, _account("gold"), {"username": "shop"})
    assert db.query(HostingPlan).count() == 1
    plan = db.query(HostingPlan).one()
    assert plan.website_limit == 50 and db.query(User).one().plan_id == plan.id


def test_cpanels_own_plans_and_resellers_packages_are_not_used(db):
    reseller = User(username="res", email="r@example.test", role="reseller", is_active=True, hashed_password="x", token_version=0)
    db.add(reseller)
    db.commit()
    db.add(HostingPlan(slug="silver", name="Silver", owner_id=reseller.id))
    db.commit()
    summary = {"username": "shop"}
    cpanel_import._apply_package(db, _account("Silver"), summary)
    cpanel_import._apply_package(db, _account("default"), summary)
    assert db.query(User).filter(User.username == "shop").one().plan_id is None
    assert any("reseller" in w for w in summary["warnings"])


def test_the_object_cache_is_switched_on_or_the_report_says_why(db, monkeypatch):
    calls = []
    monkeypatch.setattr(redis_user, "installed", lambda: True)
    monkeypatch.setattr(redis_user, "enable", lambda db, user: calls.append(user.username))
    summary = {"username": "shop"}
    cpanel_import._enable_object_cache(db, summary)
    assert calls == ["shop"] and summary["object_cache"] is True

    monkeypatch.setattr(redis_user, "enable", lambda db, user: (_ for _ in ()).throw(ValueError("Your package does not include Redis")))
    summary = {"username": "shop"}
    cpanel_import._enable_object_cache(db, summary)
    assert "object_cache" not in summary and "package does not include" in summary["warnings"][0]

    monkeypatch.setattr(redis_user, "installed", lambda: False)
    summary = {"username": "shop"}
    cpanel_import._enable_object_cache(db, summary)
    assert "not installed" in summary["warnings"][0]
