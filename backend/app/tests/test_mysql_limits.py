"""Server > MySQL: MariaDB's share of the server, and who is using it (as SNPanel's, 2026-10-17)."""
import pathlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import DatabaseAccount, User
from app.services import mysql_limits, panel_settings

HELPER = (pathlib.Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("boss", "admin"), ("acme", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    db.add(DatabaseAccount(owner_id=people["acme"].id, db_name="acme_shop", db_user="acme_shop", db_password="x"))
    db.commit()
    store, calls, sql = {}, [], []
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: dict(store))
    monkeypatch.setattr(panel_settings, "_write_raw", lambda data: store.update(data))
    monkeypatch.setattr(mysql_limits.shell, "privileged", lambda verb, helper_args=None, **kw: calls.append((verb, helper_args)))
    monkeypatch.setattr(mysql_limits.mariadb, "_run_sql", lambda text, **kw: sql.append(text))
    monkeypatch.setattr(mysql_limits, "buffer_pool_mb", lambda: 1024)
    processlist = [["41", "acme_shop", "acme_shop", "Query", "12", "Sending data", "SELECT * FROM wp_posts"],
                   ["7", "system user", "", "Query", "100", "", ""]]
    monkeypatch.setattr(mysql_limits, "_batch", lambda text: (
        processlist if "FROM information_schema.PROCESSLIST WHERE COMMAND" in text
        else [["acme_shop"]] if "WHERE ID = 41" in text
        else [["system user"]] if "WHERE ID = 7" in text
        else [] if "WHERE ID" in text
        else [["1", "0", "151", "3"]] if "@@GLOBAL.userstat" in text
        else [["acme_shop", "2", "90", "1.5", "2.0", "1000", "10"]]))
    monkeypatch.setattr(mysql_limits, "unit_usage", lambda: {"cpu_percent": 12.5, "memory_bytes": 300 << 20})
    monkeypatch.setattr(mysql_limits.time, "sleep", lambda s: None)

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
        yield SimpleNamespace(as_user=as_user, calls=calls, sql=sql, store=store)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_saving_sets_the_unit_the_option_file_and_the_running_server(env):
    answer = env.as_user("boss").put("/api/mysql/limits", json={
        "cpu_quota_percent": 150, "cpu_weight": 50, "io_weight": 50, "memory_max_mb": 2048,
        "max_user_connections": 30, "userstat": True})
    assert answer.status_code == 200, answer.text
    assert env.calls[-1] == ("mariadb-limits-apply", ["150", "50", "50", "2048", "30", "1"])
    joined = "".join(env.sql)
    assert "SET GLOBAL userstat = 1;" in joined
    # Each of the panel's database accounts gets the limit at once.
    assert "ALTER USER IF EXISTS 'acme_shop'@'localhost' WITH MAX_USER_CONNECTIONS 30;" in env.sql[-1]
    assert answer.json()["limits"]["memory_max_mb"] == 2048


def test_memory_cannot_go_under_the_buffer_pool(env):
    answer = env.as_user("boss").put("/api/mysql/limits", json={**mysql_limits.DEFAULTS, "memory_max_mb": 1200})
    assert answer.status_code == 400 and "1536" in answer.json()["detail"]
    assert env.calls == []


def test_usage_names_the_account_behind_each_database_user(env):
    body = env.as_user("boss").get("/api/mysql/usage").json()
    assert body["server"]["threads_connected"] == 3
    assert body["users"][0]["user"] == "acme_shop" and body["users"][0]["owner"] == "acme"
    assert [p["id"] for p in body["processes"]] == [41]  # MariaDB's own thread is left out
    assert body["processes"][0]["owner"] == "acme"


def test_a_statement_is_stopped_but_never_mariadbs_own(env):
    client = env.as_user("boss")
    assert client.post("/api/mysql/processes/41/kill").status_code == 200
    assert env.sql[-1] == "KILL QUERY 41;\n"
    assert client.post("/api/mysql/processes/7/kill").status_code == 403
    assert client.post("/api/mysql/processes/99/kill").status_code == 404


def test_only_an_administrator(env):
    client = env.as_user("acme")
    assert client.get("/api/mysql/usage").status_code == 403
    assert client.put("/api/mysql/limits", json=mysql_limits.DEFAULTS).status_code == 403
    assert client.post("/api/mysql/processes/41/kill").status_code == 403


def test_the_helper_checks_its_numbers_and_resets_with_empty_values():
    body = HELPER.split("  mariadb-limits-apply)", 1)[1].split(";;", 1)[0]
    assert '[[ "$value" =~ ^[0-9]{1,7}$ ]]' in body
    assert '"CPUQuota="' in body and '"MemoryMax=infinity"' in body
    assert "systemctl set-property" in body and "91-opanel-limits.cnf" in body


def test_a_new_database_account_gets_the_limit(monkeypatch):
    from app.services import mariadb
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: {"mysql_limits": {"max_user_connections": 25}})
    sent = []
    monkeypatch.setattr(mariadb, "_run_sql", lambda text, **kw: sent.append(text))
    mariadb.create_database_credentials("acme_new", "acme_new", "PasswordLongEnough1")
    assert "ALTER USER IF EXISTS 'acme_new'@'localhost' WITH MAX_USER_CONNECTIONS 25;" in sent[0]
