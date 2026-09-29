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
    assert url.startswith("https://panel.example.test:2096/api/auth/sso?token=")
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
    assert url.startswith("https://webmail.alice.test/api/auth/sso?token=")


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


RELAY = {"name": "Brevo", "host": "smtp-relay.brevo.com", "port": 587, "username": "apikey",
         "password": "relay-Secret-9", "spf_include": "include:spf.brevo.com",
         "dns_records": [{"type": "TXT", "name": "@", "value": "brevo-code:{domain}-abc"},
                         {"type": "CNAME", "name": "brevo1._domainkey", "value": "b1.{domain}.dkim.brevo.com"}]}


def _configure(env):
    return json.loads([c for c in env.calls if c.command == "mail-configure"][-1].input)


def test_a_relay_password_is_stored_encrypted_and_never_shown(env):
    res = env.as_user("root_admin").post("/api/mail/relays", json={**RELAY, "make_default": True})
    assert res.status_code == 200, res.text
    body = res.json()
    relay = body["relays"][0]
    assert body["default_relay"] == relay["id"] and relay["password_set"] and "password" not in relay
    assert "relay-Secret-9" not in json.dumps(env.store)
    sent = _configure(env)
    assert sent["relays"] == [{"id": relay["id"], "host": "smtp-relay.brevo.com", "port": 587, "tls": "starttls",
                               "username": "apikey", "password": "relay-Secret-9"}]
    assert [c for c in env.calls if c.command == "mail-configure"][-1].sensitive
    # Saving again without a password keeps it.
    res = env.as_user("root_admin").put(f"/api/mail/relays/{relay['id']}", json={**RELAY, "password": "", "port": 2525})
    assert res.status_code == 200, res.text
    sent = _configure(env)
    assert sent["relays"][0]["password"] == "relay-Secret-9" and sent["relays"][0]["port"] == 2525


def test_only_an_admin_manages_relays(env):
    assert env.as_user("alice").get("/api/mail/relays").status_code == 403
    assert env.as_user("alice").post("/api/mail/relays", json=RELAY).status_code == 403


@pytest.mark.parametrize("field,value", [
    ("host", "smtp.relay.test::25"), ("host", "a b"), ("host", "relay;rm"), ("host", "x" * 300),
    ("tls", "maybe"), ("spf_include", "include:x; rm -rf"), ("username", "a\nb"),
])
def test_relay_values_that_could_break_the_config_are_refused(env, field, value):
    res = env.as_user("root_admin").post("/api/mail/relays", json={**RELAY, field: value})
    assert res.status_code in (400, 422), (field, value)


def test_one_host_carries_one_login(env):
    assert env.as_user("root_admin").post("/api/mail/relays", json=RELAY).status_code == 200
    res = env.as_user("root_admin").post("/api/mail/relays", json={**RELAY, "name": "Second"})
    assert res.status_code == 400 and "already logs in" in res.json()["detail"]


def test_domains_route_through_their_relay_or_the_default(env):
    first = _add_domain(env, "alice", "alice.test")
    second = _add_domain(env, "alice", "alice-shop.test")
    relays = env.as_user("root_admin").post("/api/mail/relays", json=RELAY).json()["relays"]
    other = env.as_user("root_admin").post("/api/mail/relays", json={
        **RELAY, "name": "SES", "host": "email-smtp.ap-southeast-1.amazonaws.com"}).json()["relays"]
    brevo = relays[0]["id"]
    ses = next(r["id"] for r in other if r["name"] == "SES")
    assert env.as_user("root_admin").put("/api/mail/default-relay", json={"relay_id": brevo}).status_code == 200
    assert env.as_user("root_admin").put(f"/api/mail/domains/{second}/relay", json={"relay": ses}).status_code == 200
    state = env.syncs()[-1]
    assert state["default_relay"] == brevo and state["relay_routes"] == {"alice-shop.test": ses}
    assert env.as_user("root_admin").put(f"/api/mail/domains/{first}/relay", json={"relay": "direct"}).status_code == 200
    assert env.syncs()[-1]["relay_routes"] == {"alice-shop.test": ses, "alice.test": "direct"}
    # A customer sees the relay, but cannot choose it.
    res = env.as_user("alice").put(f"/api/mail/domains/{second}/relay", json={"relay": "direct"})
    assert res.status_code == 403
    # Deleting a relay sends its domains back to the default.
    env.as_user("root_admin").delete(f"/api/mail/relays/{ses}")
    assert env.syncs()[-1]["relay_routes"] == {"alice.test": "direct"}


def test_the_dns_records_follow_the_relay(env, monkeypatch):
    domain_id = _add_domain(env, "alice", "alice.test")
    env.as_user("root_admin").post("/api/mail/relays", json={**RELAY, "make_default": True})
    monkeypatch.setattr(mail, "_resolve", lambda name, rtype: [])
    view = env.as_user("alice").get(f"/api/mail/domains/{domain_id}/dns").json()
    records = {r["key"]: r for r in view["records"]}
    assert "include:spf.brevo.com" in records["spf"]["value"]
    assert records["relay-0"]["value"] == "brevo-code:alice.test-abc" and records["relay-0"]["name"] == "alice.test"
    assert records["relay-1"]["name"] == "brevo1._domainkey.alice.test"
    assert records["relay-1"]["value"] == "b1.alice.test.dkim.brevo.com"
    assert view["relay"]["effective_name"] == "Brevo" and view["relay"]["options"] == []


def test_an_owner_customises_the_mail_records(env, monkeypatch):
    domain_id = _add_domain(env, "alice", "alice.test")
    monkeypatch.setattr(mail, "_resolve", lambda name, rtype: [])
    res = env.as_user("alice").put(f"/api/mail/domains/{domain_id}/dns", json={
        "spf": "v=spf1 mx include:_spf.google.com ~all", "dmarc": "v=DMARC1; p=reject",
        "records": [{"type": "TXT", "name": "google-site-verification", "value": "abc"},
                    {"type": "CNAME", "name": "em123.alice.test", "value": "u1.wl.sendgrid.net"},
                    {"type": "MX", "name": "@", "value": "backup-mx.example.net", "priority": 20}]})
    assert res.status_code == 200, res.text
    records = {r["key"]: r for r in res.json()["records"]}
    assert records["spf"]["value"] == "v=spf1 mx include:_spf.google.com ~all" and records["spf"]["custom"]
    assert records["dmarc"]["value"] == "v=DMARC1; p=reject"
    assert records["custom-0"]["name"] == "google-site-verification.alice.test"
    assert records["custom-1"]["name"] == "em123.alice.test"
    assert records["custom-2"]["priority"] == 20
    # Empty values go back to the suggestion.
    res = env.as_user("alice").put(f"/api/mail/domains/{domain_id}/dns", json={"spf": "", "dmarc": "", "records": []})
    records = {r["key"]: r for r in res.json()["records"]}
    assert not records["spf"]["custom"] and records["dmarc"]["value"].startswith("v=DMARC1; p=quarantine")


@pytest.mark.parametrize("payload", [
    {"spf": "include:x ~all"}, {"spf": "v=spf1 mx; rm"}, {"dmarc": "p=reject"},
    {"records": [{"type": "SRV", "name": "@", "value": "x"}]},
    {"records": [{"type": "A", "name": "@", "value": "not-an-ip"}]},
    {"records": [{"type": "CNAME", "name": "a b", "value": "x.example.com"}]},
    {"records": [{"type": "TXT", "name": "@", "value": "line\nbreak"}]},
])
def test_bad_custom_records_are_refused(env, payload):
    domain_id = _add_domain(env, "alice", "alice.test")
    res = env.as_user("alice").put(f"/api/mail/domains/{domain_id}/dns", json={"spf": "", "dmarc": "", "records": [], **payload})
    assert res.status_code in (400, 422), payload


def test_another_customers_dns_cannot_be_changed(env):
    domain_id = _add_domain(env, "bob", "bob.test")
    res = env.as_user("alice").put(f"/api/mail/domains/{domain_id}/dns", json={"spf": "v=spf1 -all"})
    assert res.status_code == 404


def test_a_custom_spf_is_checked_for_every_mechanism(env, monkeypatch):
    domain_id = _add_domain(env, "alice", "alice.test")
    env.as_user("root_admin").post("/api/mail/relays", json={**RELAY, "make_default": True})
    published = {"alice.test": ["v=spf1 mx a ip4:203.0.113.7 ~all"]}
    monkeypatch.setattr(mail, "_resolve", lambda name, rtype: published.get(name, []) if rtype == "TXT" else [])
    spf = next(r for r in env.as_user("alice").get(f"/api/mail/domains/{domain_id}/dns").json()["records"]
               if r["key"] == "spf")
    assert spf["status"] == "different"  # the relay's include is missing
    published["alice.test"] = ["v=spf1 mx a ip4:203.0.113.7 include:spf.brevo.com include:other ~all"]
    spf = next(r for r in env.as_user("alice").get(f"/api/mail/domains/{domain_id}/dns").json()["records"]
               if r["key"] == "spf")
    assert spf["status"] == "ok"


def test_rspamd_history_is_normalised_filtered_and_paged(env, monkeypatch):
    rows = [{"unix_time": 1790000000 + i, "ip": "198.51.100.%d" % i, "sender_mime": f"s{i}@example.net",
             "rcpt_mime": ["info@alice.test"], "subject": "Offer %d" % i, "score": 7.25 if i % 2 else 1.1,
             "required_score": 15, "action": "add header" if i % 2 else "no action",
             "symbols": {"R_SPF_FAIL": {"score": 1.0}, "BAYES_SPAM": {"score": 5.1}, "MIME_GOOD": {"score": -0.1}}}
            for i in range(7)]

    def fake(command, helper_args=None, **kwargs):
        env.calls.append(SimpleNamespace(command=command, args=list(helper_args or []), input=None, sensitive=False))
        if command == "mail-rspamd" and helper_args == ["history"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"version": 2, "rows": rows}), stderr="")
        if command == "mail-rspamd" and helper_args == ["stat"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"scanned": 7, "spam_count": 3, "ham_count": 4,
                                                                     "actions": {"add header": 3, "no action": 4}}),
                                   stderr="")
        if command == "mail-rspamd" and helper_args and helper_args[0] == "log":
            return SimpleNamespace(returncode=0, stdout="line a\nrspamd_task_write_log: spam x\nline c\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mail.shell, "privileged", fake)
    assert env.as_user("alice").get("/api/mail/rspamd/history").status_code == 403
    page = env.as_user("root_admin").get("/api/mail/rspamd/history?per_page=2&action=add+header").json()
    assert page["total"] == 3 and len(page["items"]) == 2
    item = page["items"][0]
    assert item["action"] == "add header" and item["score"] == 7.25 and item["to"] == ["info@alice.test"]
    assert item["symbols"][0] == {"name": "BAYES_SPAM", "score": 5.1}
    assert env.as_user("root_admin").get("/api/mail/rspamd/history?q=s4@").json()["total"] == 1
    stat = env.as_user("root_admin").get("/api/mail/rspamd/stat").json()
    assert stat["scanned"] == 7 and stat["actions"]["add header"] == 3
    log = env.as_user("root_admin").get("/api/mail/rspamd/log?q=spam").json()
    assert log["lines"] == ["rspamd_task_write_log: spam x"]
    assert env.as_user("alice").get("/api/mail/rspamd/log").status_code == 403


def test_the_helper_routes_by_the_signed_domain():
    assert "relay_routes" in HELPER and "lsearch*" in HELPER
    assert "${if def:acl_m_dkim{$acl_m_dkim}{${lc:$sender_address_domain}}}" in HELPER
    assert 'hosts_require_auth = ${{lookup{{$host}}lsearch{{{M}/relay_auth}}{{*}}{{}}}}' in HELPER
    assert "public_name = LOGIN" in HELPER  # Office 365 offers LOGIN only
    body = HELPER[HELPER.index("mail_write_relay_credentials() {"):]
    body = body[:body.index("\n}\n")]
    assert "two relays with a login on" in body and 'os.chmod(tmp, mode)' in body


def test_the_rspamd_helper_is_read_only_and_loopback():
    body = HELPER[HELPER.index("  mail-rspamd)"):]
    body = body[:body.index("\n    ;;\n\n")]
    assert "http://127.0.0.1:11334/" in body and "stat|history)" in body
    assert "/var/log/rspamd/rspamd.log" in body
    assert 'level = "notice";' in HELPER


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


def test_the_sync_payload_is_saved_before_the_heredoc_takes_stdin():
    """python3 - <<'PY' reads its program from stdin, so the panel's JSON has
    to be on disk first. The first live install failed exactly here."""
    body = HELPER[HELPER.index("mail_sync_from_stdin() {"):]
    body = body[:body.index("\n}\n")]
    assert body.index('cat >"$payload"') < body.index("python3 - ")
    assert "sys.stdin" not in body
    assert 'rm -f -- "$payload"' in body


def test_an_update_refreshes_an_installed_mail_server():
    """Install-only changes never reached live boxes before; update.sh runs
    log-hygiene with the new helper, and that brings the addon up to date."""
    hygiene = HELPER[HELPER.index("  log-hygiene)"):]
    hygiene = hygiene[:hygiene.index(";;")]
    assert "( mail_refresh ) ||" in hygiene
    refresh = HELPER[HELPER.index("mail_refresh() {"):]
    refresh = refresh[:refresh.index("\n}\n")]
    assert "mail_installed || return 0" in refresh
    for step in ("mail_write_exim_config", "mail_write_dovecot_config", "mail_write_unbound_config",
                 "mail_write_rspamd_config", "mail_webmail_install", 'missing+=("$pkg")'):
        assert step in refresh, step


def test_rspamd_asks_its_own_resolver():
    """Spamhaus refuses public resolvers, which quietly disables the DNS
    blocklists; Rspamd gets a local Unbound on a port of its own."""
    assert 'nameserver = ["127.0.0.1:${MAIL_UNBOUND_PORT}"];' in HELPER
    assert "interface: 127.0.0.1" in HELPER and 'MAIL_UNBOUND_PORT="5335"' in HELPER
    assert "systemctl mask unbound-resolvconf.service" in HELPER


def test_a_suspended_mailbox_is_refused_by_a_deny_passdb():
    """nologin=y in the passwd file let an IMAP login through on the test box;
    Dovecot's deny passdb refuses the account before any password check, and
    applies to the webmail's master-user logins too."""
    assert "deny = yes" in HELPER and "${MAIL_DIR}/denied" in HELPER
    assert 'denied.append(f"{addr}:")' in HELPER
    assert "nologin=y" not in HELPER


def test_suspending_a_mailbox_drops_its_open_sessions():
    body = HELPER[HELPER.index("mail_sync_from_stdin() {"):]
    body = body[:body.index("\n}\n")]
    assert 'subprocess.run(["doveadm", "kick", addr]' in body
    assert "if addr not in previously_denied" in body


def test_removal_takes_what_apt_pulled_in_with_the_mail_server():
    """Uninstall on the first box stopped at "would also remove bsd-mailx",
    which apt had installed with Exim as a recommendation."""
    body = HELPER[HELPER.index("addon_mail_uninstall() {"):]
    body = body[:body.index("\n}\n")]
    assert 'apt-mark showauto "$name"' in body
    assert "which was installed separately" in body
