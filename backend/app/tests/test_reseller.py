"""The reseller role: customers, a share of the server, own packages.

Decided with the operator (2026-10-03): a reseller gets a total share from the
admin (customers, websites, disk, databases, mailboxes) and divides it between
its own account and its customers; it manages its customers, logs in as them,
keeps packages of its own and hosts sites of its own. Nothing server-wide.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import HostingPlan, User
from app.services import site_users

PASSWORD = "PasswordLongEnough1"


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(User(username="root_admin", email="a@example.test", role="admin", is_active=True,
                hashed_password=hash_password(PASSWORD)))
    db.add(User(username="direct", email="d@example.test", role="end_user", is_active=True,
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
    # No Linux accounts in a test.
    monkeypatch.setattr(site_users, "ensure_panel_user", lambda *a, **k: None)
    monkeypatch.setattr(site_users, "delete_panel_user", lambda *a, **k: None)
    monkeypatch.setattr(site_users, "set_panel_user_password", lambda *a, **k: None)
    client = TestClient(app)
    try:
        yield db, client
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _login(client, name):
    client.cookies.clear()
    assert client.post("/api/auth/login", data={"username": name, "password": PASSWORD}).status_code == 200


def _call(client, method, path, **kwargs):
    headers = {"X-CSRF-Token": client.cookies.get("opanel_csrf", "")}
    return client.request(method, path, headers=headers, **kwargs)


def _user(db, name):
    db.expire_all()
    return db.query(User).filter(User.username == name).one()


ACCOUNT = {"email": "x@example.com", "password": PASSWORD}
OWN = {"website_limit": 1, "storage_limit_mb": 1000, "database_limit": 1, "mailbox_limit": 2}
POOL = {"pool_user_limit": 2, "pool_website_limit": 5, "pool_storage_limit_mb": 3000,
        "pool_database_limit": 5, "pool_mailbox_limit": 10}
SMALL = {"website_limit": 2, "storage_limit_mb": 1000, "database_limit": 2, "mailbox_limit": 4}


def _reseller(client):
    _login(client, "root_admin")
    created = _call(client, "POST", "/api/users", json={"username": "shop", "role": "reseller", **ACCOUNT, **OWN, **POOL})
    assert created.status_code == 200, created.text
    assert created.json()["role"] == "reseller" and created.json()["pool_website_limit"] == 5
    _login(client, "shop")


def test_a_reseller_creates_customers_inside_its_share(env):
    db, client = env
    _reseller(client)
    made = _call(client, "POST", "/api/users", json={"username": "cust1", "role": "admin", **ACCOUNT, **SMALL})
    assert made.status_code == 200, made.text
    # Always its own end user, whatever was asked for.
    assert made.json()["role"] == "end_user" and made.json()["reseller_id"] == _user(db, "shop").id

    # websites: own 1 + 2 + 3 = 6 > 5.
    over = _call(client, "POST", "/api/users", json={"username": "cust2", **ACCOUNT, **{**SMALL, "website_limit": 3}})
    assert over.status_code == 400 and "websites" in over.json()["detail"]
    # Unlimited storage cannot come out of a limited share.
    blank = _call(client, "POST", "/api/users", json={"username": "cust2", **ACCOUNT, **{**SMALL, "storage_limit_mb": 0}})
    assert blank.status_code == 400 and "storage" in blank.json()["detail"]

    assert _call(client, "POST", "/api/users", json={"username": "cust2", **ACCOUNT, **{**SMALL, "website_limit": 1}}).status_code == 200
    third = _call(client, "POST", "/api/users", json={"username": "cust3", **ACCOUNT, "website_limit": 0,
                                                     "storage_limit_mb": 1, "database_limit": 1, "mailbox_limit": 1})
    assert third.status_code == 400 and "customers" in third.json()["detail"]

    pool = _call(client, "GET", "/api/users/pool").json()
    assert pool["customers"] == 2 and pool["allocated_website_limit"] == 4 and pool["pool_website_limit"] == 5


def test_a_reseller_sees_and_manages_its_customers_only(env):
    db, client = env
    _reseller(client)
    cust = _call(client, "POST", "/api/users", json={"username": "cust1", **ACCOUNT, **SMALL}).json()
    direct = _user(db, "direct")

    listed = [row["username"] for row in _call(client, "GET", "/api/users").json()]
    assert listed == ["cust1"]
    assert _call(client, "PATCH", f"/api/users/{direct.id}", json={"is_active": False}).status_code == 404
    assert _call(client, "DELETE", f"/api/users/{direct.id}").status_code == 404
    assert _call(client, "POST", f"/api/users/{direct.id}/password", json={"password": PASSWORD + "x"}).status_code == 404

    # Its customers: suspend, limits within the share, but no roles.
    assert _call(client, "PATCH", f"/api/users/{cust['id']}", json={"is_active": False}).json()["is_active"] is False
    assert _call(client, "PATCH", f"/api/users/{cust['id']}", json={"website_limit": 4}).status_code == 200
    assert _call(client, "PATCH", f"/api/users/{cust['id']}", json={"website_limit": 5}).status_code == 400
    assert _call(client, "PATCH", f"/api/users/{cust['id']}", json={"role": "reseller"}).status_code == 403
    # Its own limits are the admin's to set.
    assert _call(client, "PATCH", f"/api/users/{_user(db, 'shop').id}", json={"website_limit": 9}).status_code == 403
    # Nothing server-wide.
    assert _call(client, "GET", "/api/users/audit/log").status_code == 403


def test_a_reseller_logs_in_as_a_customer_and_comes_back(env):
    db, client = env
    _reseller(client)
    cust = _call(client, "POST", "/api/users", json={"username": "cust1", **ACCOUNT, **SMALL}).json()
    assert _call(client, "POST", f"/api/auth/impersonate/{_user(db, 'direct').id}").status_code == 404
    assert _call(client, "POST", f"/api/auth/impersonate/{cust['id']}").status_code == 200
    me = client.get("/api/auth/session").json()["user"]
    assert me["username"] == "cust1" and me["impersonator"] == "shop"
    assert _call(client, "POST", "/api/auth/impersonation/return").status_code == 200
    assert client.get("/api/auth/session").json()["user"]["username"] == "shop"


def test_a_reseller_keeps_packages_of_its_own(env):
    db, client = env
    _login(client, "root_admin")
    assert _call(client, "POST", "/api/plans", json={"slug": "basic", "name": "Admin basic"}).status_code == 200
    _reseller(client)
    made = _call(client, "POST", "/api/plans", json={"slug": "basic", "name": "Shop basic", "website_limit": 2,
                                                     "database_limit": 3, "mailbox_limit": 4})
    assert made.status_code == 200, made.text
    assert made.json()["slug"] == "shop-basic" and made.json()["mailbox_limit"] == 4
    assert [p["name"] for p in _call(client, "GET", "/api/plans").json()] == ["Shop basic"]
    admin_plan = db.query(HostingPlan).filter(HostingPlan.slug == "basic").one()
    assert _call(client, "PATCH", f"/api/plans/{admin_plan.id}", json={"name": "taken"}).status_code == 404
    assert _call(client, "DELETE", f"/api/plans/{admin_plan.id}").status_code == 404

    _login(client, "root_admin")
    assert [p["name"] for p in _call(client, "GET", "/api/plans").json()] == ["Admin basic"]


def test_the_admin_keeps_the_shares_consistent(env):
    db, client = env
    _reseller(client)
    cust = _call(client, "POST", "/api/users", json={"username": "cust1", **ACCOUNT, **SMALL}).json()
    _login(client, "root_admin")
    shop = _user(db, "shop")
    # A share smaller than what is already handed out.
    assert _call(client, "PATCH", f"/api/users/{shop.id}", json={"pool_website_limit": 2}).status_code == 400
    assert _call(client, "PATCH", f"/api/users/{shop.id}", json={"pool_website_limit": 3}).status_code == 200
    # A reseller with customers keeps its role and its account.
    assert _call(client, "PATCH", f"/api/users/{shop.id}", json={"role": "end_user"}).status_code == 400
    assert _call(client, "DELETE", f"/api/users/{shop.id}").status_code == 400
    # Moving the admin's own customer under the reseller must fit its share.
    direct = _user(db, "direct")
    moved = _call(client, "PATCH", f"/api/users/{direct.id}", json={"reseller_id": shop.id})
    assert moved.status_code == 400  # direct has 5 websites; 1 + 2 + 5 > 3
    assert _call(client, "PATCH", f"/api/users/{cust['id']}", json={"reseller_id": 0}).json()["reseller_id"] is None
    assert _call(client, "GET", f"/api/users/{shop.id}/pool").json()["customers"] == 0
    assert _call(client, "PATCH", f"/api/users/{shop.id}", json={"role": "end_user"}).status_code == 200
    assert _user(db, "shop").pool_website_limit == 0


def test_an_end_user_has_no_reseller_powers(env):
    db, client = env
    _login(client, "direct")
    assert _call(client, "GET", "/api/users").status_code == 403
    assert _call(client, "POST", "/api/users", json={"username": "sneaky", **ACCOUNT}).status_code == 403
    assert _call(client, "GET", "/api/plans").status_code == 403
    assert _call(client, "POST", f"/api/auth/impersonate/{_user(db, 'root_admin').id}").status_code == 403


def _site(db, owner, domain):
    from app.models.entities import DatabaseAccount, Website

    site = Website(domain=domain, owner_id=owner.id, root_path=f"/home/{owner.username}/{domain}",
                   document_root="public_html", linux_user=owner.username, php_version="8.4",
                   app_type="php", status="active")
    db.add(site)
    db.flush()
    db.add(DatabaseAccount(owner_id=owner.id, website_id=site.id, db_name=domain.split(".")[0],
                           db_user=domain.split(".")[0], db_password="x"))
    db.commit()
    return site


def test_a_reseller_sees_its_own_and_its_customers_sites_and_nobody_elses(env, monkeypatch):
    from app.api import websites as websites_api

    db, client = env
    _reseller(client)
    _call(client, "POST", "/api/users", json={"username": "cust1", **ACCOUNT, **SMALL})
    own = _site(db, _user(db, "shop"), "shopsite.com")
    theirs = _site(db, _user(db, "cust1"), "custsite.com")
    other = _site(db, _user(db, "direct"), "othersite.com")
    monkeypatch.setattr(websites_api.openlitespeed, "read_site_log", lambda domain, kind, lines: {"domain": domain, "kind": kind, "path": "/x", "lines": lines})

    domains = sorted(w["domain"] for w in _call(client, "GET", "/api/websites").json())
    assert domains == ["custsite.com", "shopsite.com"]
    names = sorted(d["db_name"] for d in _call(client, "GET", "/api/databases").json())
    assert names == ["custsite", "shopsite"]
    assert _call(client, "GET", f"/api/websites/{theirs.id}/logs").status_code == 200
    assert _call(client, "GET", f"/api/websites/{other.id}/logs").status_code == 403

    # It may suspend a customer's site, not its own, and nobody else's.
    assert _call(client, "PATCH", f"/api/websites/{own.id}", json={"status": "suspended"}).status_code == 403
    assert _call(client, "PATCH", f"/api/websites/{other.id}", json={"status": "suspended"}).status_code == 403

    # The customer still sees only its own.
    _login(client, "cust1")
    assert [w["domain"] for w in _call(client, "GET", "/api/websites").json()] == ["custsite.com"]
