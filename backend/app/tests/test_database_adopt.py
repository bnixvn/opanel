"""Databases MariaDB has and the panel does not, and adopting one (as SNPanel's, 2026-10-17).

An administrator lists them with whoever can reach each, and makes one an
account's: through an account it already has - only when that account reaches
nothing else - or a new one, with a password the panel knows. Other accounts'
exact grants go; a wider grant is only named.
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
from app.services import mariadb


def test_a_grant_names_a_pattern():
    assert mariadb.grant_matches("shop_db", "shop_db") and mariadb.grant_matches("shop1db", "shop_db")
    assert mariadb.grant_matches("shop_db", "shop\\_db") and not mariadb.grant_matches("shop1db", "shop\\_db")
    assert mariadb.grant_matches("anything", "%") and not mariadb.grant_is_literal("shop_db", "shop%")
    assert mariadb.grant_is_literal("shop_db", "shop\\_db") and mariadb.grant_is_literal("shop_db", "shop_db")


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("boss", "admin"), ("carol", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    site = Website(domain="carol.example", owner_id=people["carol"].id, root_path="/home/carol/carol.example")
    db.add(site)
    db.add(DatabaseAccount(owner_id=people["carol"].id, db_name="carol_main", db_user="carol_main", db_password="x"))
    db.commit()

    # MariaDB as the tests see it.
    maria = SimpleNamespace(
        schemas=[{"name": "carol_main", "size_bytes": 10, "tables": 1},
                 {"name": "legacy", "size_bytes": 2048, "tables": 12},
                 {"name": "Odd-Name", "size_bytes": 0, "tables": 0}],
        grants=[{"user": "legacy", "host": "localhost", "db": "legacy"},
                {"user": "other", "host": "localhost", "db": "legacy"},
                {"user": "wide", "host": "%", "db": "leg%"},
                {"user": "carol_main", "host": "localhost", "db": "carol_main"}],
        hosts={"legacy": ["localhost"], "remote": ["%", "localhost"]},
        beyond={"legacy": []},
        adopted=[], revoked=[],
    )
    monkeypatch.setattr(mariadb, "schemas", lambda: maria.schemas)
    monkeypatch.setattr(mariadb, "db_grants", lambda: [g for g in maria.grants
                                                       if (g["user"], g["host"], g["db"]) not in maria.revoked])
    monkeypatch.setattr(mariadb, "schema_exists", lambda name: any(s["name"] == name for s in maria.schemas))
    monkeypatch.setattr(mariadb, "account_hosts", lambda user: maria.hosts.get(user, []))
    monkeypatch.setattr(mariadb, "reaches_beyond", lambda user, name: maria.beyond.get(user, []))
    monkeypatch.setattr(mariadb, "adopt_account",
                        lambda name, user, password, existing: maria.adopted.append((name, user, existing)))
    monkeypatch.setattr(mariadb, "revoke_db_grant", lambda user, host, pattern: maria.revoked.append((user, host, pattern)))
    monkeypatch.setattr(databases_api, "_point_site_configs", lambda *a: ["public_html/wp-config.php"])

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
        yield SimpleNamespace(db=db, people=people, site=site, maria=maria, as_user=as_user)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_the_list_leaves_out_what_the_panel_manages(env):
    answer = env.as_user("boss").get("/api/databases/unmanaged")
    assert answer.status_code == 200, answer.text
    found = {item["db_name"]: item for item in answer.json()["databases"]}
    assert set(found) == {"legacy", "Odd-Name"}
    assert found["Odd-Name"]["adoptable"] is False and found["Odd-Name"]["reason"]
    who = {g["user"]: g for g in found["legacy"]["grantees"]}
    assert who["legacy"]["adoptable"] and who["other"]["adoptable"]
    assert who["wide"]["adoptable"] is False
    assert found["legacy"]["size_bytes"] == 2048 and found["legacy"]["tables"] == 12


def test_only_an_administrator_sees_or_adopts(env):
    client = env.as_user("carol")
    assert client.get("/api/databases/unmanaged").status_code == 403
    assert client.post("/api/databases/adopt", json={"db_name": "legacy", "owner_id": env.people["carol"].id}).status_code == 403


def test_adopting_its_own_account_takes_the_others_grants_away(env):
    answer = env.as_user("boss").post("/api/databases/adopt", json={
        "db_name": "legacy", "owner_id": env.people["carol"].id, "website_id": env.site.id, "db_user": "legacy"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["db_password"] and body["adopted_account"] == "existing"
    assert body["revoked"] == ["other@localhost"]
    assert any("wide@%" in warning for warning in body["warnings"])
    assert body["configs_updated"] == ["public_html/wp-config.php"]
    assert env.maria.adopted == [("legacy", "legacy", True)]
    row = env.db.query(DatabaseAccount).filter_by(db_name="legacy").one()
    assert row.owner_id == env.people["carol"].id and row.website_id == env.site.id and row.db_user == "legacy"


def test_others_keep_their_access_when_asked(env):
    answer = env.as_user("boss").post("/api/databases/adopt", json={
        "db_name": "legacy", "owner_id": env.people["carol"].id, "new_user": "carol_legacy", "revoke_others": False})
    assert answer.status_code == 200, answer.text
    assert answer.json()["revoked"] == [] and env.maria.revoked == []
    assert env.maria.adopted == [("legacy", "carol_legacy", False)]


def test_an_account_that_reaches_more_is_not_handed_over(env):
    env.maria.beyond["legacy"] = ["*.*"]
    answer = env.as_user("boss").post("/api/databases/adopt", json={
        "db_name": "legacy", "owner_id": env.people["carol"].id, "db_user": "legacy"})
    assert answer.status_code == 409 and "*.*" in answer.json()["detail"]
    assert env.maria.adopted == [] and not env.db.query(DatabaseAccount).filter_by(db_name="legacy").first()


def test_a_new_account_must_be_new_and_not_another_databases(env):
    client = env.as_user("boss")
    carol = env.people["carol"].id
    assert client.post("/api/databases/adopt", json={"db_name": "legacy", "owner_id": carol, "new_user": "remote"}).status_code == 409
    assert client.post("/api/databases/adopt", json={"db_name": "legacy", "owner_id": carol, "db_user": "remote"}).status_code == 409
    assert client.post("/api/databases/adopt", json={"db_name": "legacy", "owner_id": carol, "new_user": "carol_main"}).status_code == 409
    assert client.post("/api/databases/adopt", json={"db_name": "carol_main", "owner_id": carol}).status_code == 409
    assert client.post("/api/databases/adopt", json={"db_name": "gone", "owner_id": carol}).status_code == 404
    assert env.maria.adopted == []
