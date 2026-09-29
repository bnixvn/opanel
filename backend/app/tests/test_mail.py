"""Email addon: who may manage which mail, what reaches the helper, and SSO."""
import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
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
from app.models.entities import MailDomain, Mailbox, User, Website, WebsiteAlias
from app.services import addons, mail

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
DKIM_KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "A" * 300 + "IDAQAB"
SSO_SECRET = "s" * 64


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("root_admin", "admin"), ("alice", "end_user"), ("bob", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"), mailbox_limit=3)
        db.add(people[name])
    db.commit()
    sites = {}
    for owner, domain in (("alice", "alice.test"), ("bob", "bob.test")):
        sites[owner] = Website(domain=domain, owner_id=people[owner].id, root_path=f"/home/{owner}/{domain}",
                               document_root="public_html", linux_user=owner, php_version="8.3",
                               app_type="php", status="active")
        db.add(sites[owner])
    db.commit()
    db.add(WebsiteAlias(website_id=sites["alice"].id, domain="alice-shop.test", mode="alias"))
    db.commit()

    calls = []

    def fake_privileged(command, helper_args=None, input=None, sensitive=False, fallback=None, check=True):
        calls.append(SimpleNamespace(command=command, args=list(helper_args or []), input=input, sensitive=sensitive))
        stdout = DKIM_KEY + "\n" if command == "mail-dkim" else ""
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(mail.shell, "privileged", fake_privileged)
    monkeypatch.setattr(mail, "MARKER", tmp_path / "installed")
    (tmp_path / "installed").write_text("")
    secret_file = tmp_path / "mail-sso.secret"
    secret_file.write_text(SSO_SECRET + "\n")
    monkeypatch.setattr(mail, "SSO_SECRET_FILE", secret_file)
    monkeypatch.setattr(mail, "hostname", lambda: "panel.example.test")
    monkeypatch.setattr(mail.network, "detect_addresses", lambda: {"ipv4": ["203.0.113.7"], "ipv6": []})
    store = {}
    monkeypatch.setattr(addons, "_read_state", lambda: json.loads(json.dumps(store)))
    monkeypatch.setattr(addons, "_write_state", lambda data: store.clear() or store.update(data))

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    state = {"user": people["alice"]}
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)

    def as_user(name):
        state["user"] = db.get(User, people[name].id)
        return client

    def syncs():
        return [json.loads(c.input) for c in calls if c.command == "mail-sync"]

    try:
        yield SimpleNamespace(db=db, client=client, people=people, sites=sites, calls=calls, as_user=as_user,
                              syncs=syncs, store=store)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def _add_domain(env, user, domain):
    res = env.as_user(user).post("/api/mail/domains", json={"domain": domain})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _add_mailbox(env, user, domain_id, local="info", password="Secret123x", **extra):
    return env.as_user(user).post("/api/mail/mailboxes", json={
        "domain_id": domain_id, "local_part": local, "password": password, **extra})


# ---------------------------------------------------------------------------
# domains
# ---------------------------------------------------------------------------
def test_a_customer_turns_mail_on_for_their_own_website_and_alias(env):
    _add_domain(env, "alice", "alice.test")
    _add_domain(env, "alice", "alice-shop.test")
    names = {row.domain: row.owner_id for row in env.db.query(MailDomain).all()}
    assert names == {"alice.test": env.people["alice"].id, "alice-shop.test": env.people["alice"].id}
    assert [c.args for c in env.calls if c.command == "mail-dkim"] == [["alice.test"], ["alice-shop.test"]]


def test_a_customer_cannot_take_another_accounts_or_an_unknown_domain(env):
    for domain in ("bob.test", "nobody.test"):
        res = env.as_user("alice").post("/api/mail/domains", json={"domain": domain})
        assert res.status_code == 403, domain
    assert env.db.query(MailDomain).count() == 0


def test_an_admin_adds_a_domain_for_the_website_owner(env):
    _add_domain(env, "root_admin", "bob.test")
    row = env.db.query(MailDomain).one()
    assert row.owner_id == env.people["bob"].id


def test_nothing_happens_while_the_addon_is_not_installed(env):
    mail.MARKER.unlink()
    res = env.as_user("alice").post("/api/mail/domains", json={"domain": "alice.test"})
    assert res.status_code == 409
    assert env.as_user("alice").get("/api/mail/overview").json()["installed"] is False


def test_customers_see_only_their_own_domains(env):
    _add_domain(env, "alice", "alice.test")
    _add_domain(env, "bob", "bob.test")
    alice = env.as_user("alice").get("/api/mail/overview").json()
    assert [d["domain"] for d in alice["domains"]] == ["alice.test"]
    assert alice["candidates"] == ["alice-shop.test"]
    admin = env.as_user("root_admin").get("/api/mail/overview").json()
    assert {d["domain"] for d in admin["domains"]} == {"alice.test", "bob.test"}
    bob_id = next(d["id"] for d in admin["domains"] if d["domain"] == "bob.test")
    assert env.as_user("alice").get(f"/api/mail/domains/{bob_id}/dns").status_code == 404


def test_deleting_a_domain_needs_its_name_and_purges_after_the_sync(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    assert env.as_user("alice").delete(f"/api/mail/domains/{domain_id}").status_code == 400
    res = env.as_user("alice").delete(f"/api/mail/domains/{domain_id}?confirm=alice.test")
    assert res.status_code == 200
    commands = [c.command for c in env.calls]
    assert commands.index("mail-purge-domain") > len(commands) - 1 - commands[::-1].index("mail-sync")
    assert env.syncs()[-1]["domains"] == []


def test_the_catch_all_must_exist_when_it_points_inside_the_domain(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    res = env.as_user("alice").put(f"/api/mail/domains/{domain_id}", json={"catch_all": "nobody@alice.test"})
    assert res.status_code == 400
    assert _add_mailbox(env, "alice", domain_id, "info").status_code == 200
    res = env.as_user("alice").put(f"/api/mail/domains/{domain_id}", json={"catch_all": "info@alice.test"})
    assert res.status_code == 200
    assert env.syncs()[-1]["domains"] == [{"domain": "alice.test", "catch_all": "info@alice.test"}]


def test_dns_records_carry_the_dkim_key_and_point_at_the_panel_host(env, monkeypatch):
    domain_id = _add_domain(env, "alice", "alice.test")
    monkeypatch.setattr(mail, "_resolve", lambda name, rtype: [])
    records = {r["key"]: r for r in env.as_user("alice").get(f"/api/mail/domains/{domain_id}/dns").json()["records"]}
    assert records["mx"]["value"] == "panel.example.test"
    assert records["dkim"]["name"] == "opanel._domainkey.alice.test"
    assert records["dkim"]["value"] == f"v=DKIM1; k=rsa; p={DKIM_KEY}"
    assert "ip4:203.0.113.7" in records["spf"]["value"]
    assert records["dmarc"]["value"].startswith("v=DMARC1")
    assert all(r["status"] == "missing" for r in records.values())


# ---------------------------------------------------------------------------
# mailboxes
# ---------------------------------------------------------------------------
def test_a_mailbox_reaches_the_helper_as_a_dovecot_hash_on_stdin(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    res = _add_mailbox(env, "alice", domain_id, "Info", quota_mb=500)
    assert res.status_code == 200, res.text
    assert res.json()["address"] == "info@alice.test"
    sync = [c for c in env.calls if c.command == "mail-sync"][-1]
    assert sync.args == [] and sync.sensitive
    state = json.loads(sync.input)
    box = state["mailboxes"][0]
    assert box["address"] == "info@alice.test" and box["quota_mb"] == 500 and box["enabled"] is True
    assert re.fullmatch(r"\{BLF-CRYPT\}\$2y\$10\$[A-Za-z0-9./]{53}", box["hash"])
    assert "Secret123x" not in sync.input
    assert state["senders"] == {"info@alice.test": ["alice.test"]}
    assert state["local_senders"] == {"alice": ["alice.test"]}


def test_a_mailbox_may_send_as_every_domain_of_its_account_and_no_other(env):
    first = _add_domain(env, "alice", "alice.test")
    _add_domain(env, "alice", "alice-shop.test")
    _add_domain(env, "bob", "bob.test")
    _add_mailbox(env, "alice", first, "info")
    state = env.syncs()[-1]
    assert sorted(state["senders"]["info@alice.test"]) == ["alice-shop.test", "alice.test"]
    assert sorted(state["local_senders"]["alice"]) == ["alice-shop.test", "alice.test"]
    assert state["local_senders"]["bob"] == ["bob.test"]


def test_the_mailbox_limit_holds_for_customers_but_not_for_the_admin(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    for local in ("a1", "a2", "a3"):
        assert _add_mailbox(env, "alice", domain_id, local).status_code == 200
    res = _add_mailbox(env, "alice", domain_id, "a4")
    assert res.status_code == 400 and "limit" in res.json()["detail"].lower()
    assert _add_mailbox(env, "root_admin", domain_id, "a4").status_code == 200


def test_customers_cannot_make_an_unlimited_mailbox(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    assert _add_mailbox(env, "alice", domain_id, "big", quota_mb=0).status_code == 400
    assert _add_mailbox(env, "root_admin", domain_id, "big", quota_mb=0).status_code == 200


@pytest.mark.parametrize("password", ["short1", "lettersonly", "12345678901", "info2024abc"])
def test_weak_passwords_are_refused(env, password):
    domain_id = _add_domain(env, "alice", "alice.test")
    assert _add_mailbox(env, "alice", domain_id, "info", password=password).status_code in (400, 422)


@pytest.mark.parametrize("local", ["-x", "a..b", "a b", "a@b", "ümlaut", "x" * 65])
def test_odd_mailbox_names_are_refused(env, local):
    domain_id = _add_domain(env, "alice", "alice.test")
    assert _add_mailbox(env, "alice", domain_id, local).status_code in (400, 422)


def test_another_customers_mailbox_is_not_found(env):
    domain_id = _add_domain(env, "bob", "bob.test")
    box = _add_mailbox(env, "bob", domain_id, "info").json()
    for method, path in (("put", f"/api/mail/mailboxes/{box['id']}"), ("delete", f"/api/mail/mailboxes/{box['id']}"),
                         ("post", f"/api/mail/mailboxes/{box['id']}/webmail")):
        client = env.as_user("alice")
        res = client.request(method.upper(), path, json={"enabled": False} if method == "put" else None)
        assert res.status_code == 404, path
    listed = env.as_user("alice").get("/api/mail/mailboxes").json()
    assert listed["total"] == 0


def test_suspending_a_mailbox_keeps_it_and_tells_the_helper(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    box = _add_mailbox(env, "alice", domain_id, "info").json()
    assert env.as_user("alice").put(f"/api/mail/mailboxes/{box['id']}", json={"enabled": False}).status_code == 200
    assert env.syncs()[-1]["mailboxes"][0]["enabled"] is False
    res = env.as_user("alice").post(f"/api/mail/mailboxes/{box['id']}/webmail")
    assert res.status_code == 400


def test_deleting_a_mailbox_removes_its_mail_after_the_sync(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    box = _add_mailbox(env, "alice", domain_id, "info").json()
    assert env.as_user("alice").delete(f"/api/mail/mailboxes/{box['id']}").status_code == 200
    purge = [c for c in env.calls if c.command == "mail-purge-mailbox"]
    assert [c.args for c in purge] == [["info@alice.test"]]
    assert env.syncs()[-1]["mailboxes"] == []


# ---------------------------------------------------------------------------
# forwarders
# ---------------------------------------------------------------------------
def test_a_forwarder_is_synced_with_its_destinations(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    res = env.as_user("alice").post("/api/mail/forwarders", json={
        "domain_id": domain_id, "local_part": "sales", "destinations": ["A@Gmail.com, b@example.org"]})
    assert res.status_code == 200, res.text
    assert env.syncs()[-1]["forwarders"] == [{"address": "sales@alice.test", "to": ["a@gmail.com", "b@example.org"]}]


def test_a_forwarder_cannot_point_at_itself_or_at_nonsense(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    for destinations in (["sales@alice.test"], ["not-an-address"], ["x@y"]):
        res = env.as_user("alice").post("/api/mail/forwarders", json={
            "domain_id": domain_id, "local_part": "sales", "destinations": destinations})
        assert res.status_code == 400, destinations


# ---------------------------------------------------------------------------
# webmail sign-on
# ---------------------------------------------------------------------------
def _b64decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_the_webmail_link_is_signed_short_lived_and_names_the_mailbox(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    box = _add_mailbox(env, "alice", domain_id, "info").json()
    res = env.as_user("alice").post(f"/api/mail/mailboxes/{box['id']}/webmail")
    assert res.status_code == 200, res.text
    url = res.json()["url"]
    assert url.startswith("https://panel.example.test:2096/sso?token=")
    payload, signature = url.split("token=", 1)[1].split(".")
    expected = hmac.new(SSO_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    assert hmac.compare_digest(expected, _b64decode(signature))
    claims = json.loads(_b64decode(payload))
    assert claims["email"] == "info@alice.test"
    assert 0 < claims["exp"] - time.time() <= 300
    assert 16 <= len(claims["nonce"]) <= 128
    second = env.as_user("alice").post(f"/api/mail/mailboxes/{box['id']}/webmail").json()["url"]
    assert second != url


def test_a_domain_with_its_own_webmail_host_links_there(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    box = _add_mailbox(env, "alice", domain_id, "info").json()
    assert env.as_user("alice").post(f"/api/mail/domains/{domain_id}/webmail-host",
                                     json={"enabled": True}).status_code == 200
    call = [c for c in env.calls if c.command == "mail-webmail-host"][-1]
    assert call.args[:2] == ["alice.test", "on"]
    url = env.as_user("alice").post(f"/api/mail/mailboxes/{box['id']}/webmail").json()["url"]
    assert url.startswith("https://webmail.alice.test/sso?token=")


def test_a_website_cannot_take_a_webmail_host(env):
    domain_id = _add_domain(env, "alice", "alice.test")
    env.as_user("alice").post(f"/api/mail/domains/{domain_id}/webmail-host", json={"enabled": True})
    from app.api import websites

    assert websites._hostname_conflicts(env.db, "webmail.alice.test")
    assert not websites._hostname_conflicts(env.db, "webmail.bob.test")


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def test_only_an_admin_reads_or_changes_mail_settings(env):
    assert env.as_user("alice").get("/api/mail/settings").status_code == 403
    assert env.as_user("alice").put("/api/mail/settings", json={"greylisting": False}).status_code == 403


def test_the_relay_password_is_stored_encrypted_and_never_shown(env):
    res = env.as_user("root_admin").put("/api/mail/settings", json={
        "smarthost_enabled": True, "smarthost_host": "smtp.relay.test", "smarthost_port": 587,
        "smarthost_username": "apikey", "smarthost_password": "relay-Secret-9"})
    assert res.status_code == 200, res.text
    shown = res.json()["settings"]
    assert "smarthost_password" not in shown and shown["smarthost_password_set"] is True
    assert "relay-Secret-9" not in json.dumps(env.store)
    configure = [c for c in env.calls if c.command == "mail-configure"][-1]
    assert configure.sensitive and configure.args == []
    sent = json.loads(configure.input)
    assert sent["smarthost"] == {"host": "smtp.relay.test", "port": 587, "username": "apikey",
                                 "password": "relay-Secret-9"}
    # Saving again without a password keeps the stored one.
    env.as_user("root_admin").put("/api/mail/settings", json={"auth_rate_per_hour": 100})
    sent = json.loads([c for c in env.calls if c.command == "mail-configure"][-1].input)
    assert sent["smarthost"]["password"] == "relay-Secret-9" and sent["auth_rate_per_hour"] == 100


def test_a_relay_host_that_could_break_the_config_is_refused(env):
    for host in ("smtp.relay.test::25", "a b", "relay;rm", "x" * 300):
        res = env.as_user("root_admin").put("/api/mail/settings", json={
            "smarthost_enabled": True, "smarthost_host": host})
        assert res.status_code in (400, 422), host


def test_deleting_an_account_takes_its_mail(env):
    _add_domain(env, "bob", "bob.test")
    _add_domain(env, "alice", "alice.test")
    bob = env.db.get(User, env.people["bob"].id)
    assert mail.delete_for_owner(env.db, bob) == ["bob.test"]
    env.db.commit()
    assert [row.domain for row in env.db.query(MailDomain).all()] == ["alice.test"]
    assert [d["domain"] for d in env.syncs()[-1]["domains"]] == ["alice.test"]
    assert [c.args for c in env.calls if c.command == "mail-purge-domain"] == [["bob.test"]]
    source = (PROJECT_ROOT / "backend" / "app" / "api" / "users.py").read_text(encoding="utf-8")
    assert "mail.delete_for_owner(db, user)" in source


# ---------------------------------------------------------------------------
# the helper
# ---------------------------------------------------------------------------
def test_the_master_login_is_loopback_only():
    assert "allow_nets=127.0.0.1/32,::1/128" in HELPER
    assert "master = yes" in HELPER and "pass = yes" in HELPER


def test_the_webmail_is_pinned_to_a_commit():
    match = re.search(r'^MAIL_WEBMAIL_COMMIT="([0-9a-f]{40})"$', HELPER, re.M)
    assert match, "the webmail must be pinned to a full commit hash"
    assert 'checkout --quiet --force "$MAIL_WEBMAIL_COMMIT"' in HELPER
    assert "does not match the pinned version" in HELPER


def test_the_webmail_admin_area_is_not_served():
    assert "RewriteRule ^/?(admin|api/admin)(/.*)?$ - [F,L]" in HELPER


def test_mail_state_and_settings_travel_on_stdin():
    for case in ("mail-sync", "mail-configure"):
        body = HELPER[HELPER.index(f"  {case})"):]
        body = body[:body.index(";;")]
        assert "$1" not in body and "${1" not in body, case


def test_passwords_never_reach_a_command_line():
    body = HELPER[HELPER.index("mail_webmail_write_env() {"):]
    body = body[:body.index("\n}\n")]
    assert "openssl passwd -6 -stdin" in body
    assert "-p \"$" not in body


def test_the_helper_and_the_panel_agree_on_mailbox_names():
    assert mail.LOCAL_PART_RE.pattern.strip("^$") in HELPER
