"""A database made for another account, on one of its websites (as SNPanel's, 2026-10-17).

An administrator or a reseller makes it for an account it manages; it can sit
on one of that account's websites, so the account's backups carry it; the
owner's package limit applies, and only an administrator may pass it.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import databases as databases_api
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import DatabaseAccount, User, Website


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role, extra in (("boss", "admin", {}), ("seller", "reseller", {}), ("carol", "end_user", {"database_limit": 1}),
                              ("dave", "end_user", {})):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"), **extra)
        db.add(people[name])
    db.commit()
    people["carol"].reseller_id = people["seller"].id
    sites = {name: Website(domain=f"{name}.example", owner_id=people[name].id, root_path=f"/home/{name}/{name}.example")
             for name in ("carol", "dave")}
    db.add_all(sites.values())
    db.commit()
    monkeypatch.setattr(databases_api.mariadb, "identifier_in_use", lambda *a: "")
    monkeypatch.setattr(databases_api.mariadb, "create_database_credentials", lambda *a: None)

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    state = {"user": people["boss"]}
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)

    def as_user(name):
        state["user"] = db.get(User, people[name].id)
        return client

    try:
        yield SimpleNamespace(db=db, people=people, sites=sites, as_user=as_user)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def _create(client, name, **extra):
    return client.post("/api/databases", json={"db_name": name, **extra})


def test_a_reseller_makes_one_for_its_customer_on_the_customers_website(env):
    answer = _create(env.as_user("seller"), "carol_shop", owner_id=env.people["carol"].id, website_id=env.sites["carol"].id)
    assert answer.status_code == 200, answer.text
    item = env.db.query(DatabaseAccount).filter_by(db_name="carol_shop").one()
    assert item.owner_id == env.people["carol"].id and item.website_id == env.sites["carol"].id


def test_not_for_an_account_the_caller_does_not_manage(env):
    assert _create(env.as_user("seller"), "dave_shop", owner_id=env.people["dave"].id).status_code == 403


def test_the_website_must_be_the_owners(env):
    answer = _create(env.as_user("boss"), "carol_x", owner_id=env.people["carol"].id, website_id=env.sites["dave"].id)
    assert answer.status_code == 400


def test_the_owners_limit_applies_and_only_an_administrator_passes_it(env):
    assert _create(env.as_user("seller"), "carol_one", owner_id=env.people["carol"].id).status_code == 200
    assert _create(env.as_user("seller"), "carol_two", owner_id=env.people["carol"].id, force=True).status_code == 403
    assert _create(env.as_user("boss"), "carol_two", owner_id=env.people["carol"].id).status_code == 403
    assert _create(env.as_user("boss"), "carol_two", owner_id=env.people["carol"].id, force=True).status_code == 200
