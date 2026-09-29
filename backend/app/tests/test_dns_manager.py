"""DNS Manager: who may manage which zone, what reaches PowerDNS, and the
records the rest of the panel keeps in step."""
import json
import re
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
from app.models.entities import DnsZone, MailDomain, User, Website, WebsiteAlias
from app.services import addons, dns_manager, mail

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


class FakePowerDNS:
    """Just enough of PowerDNS's zone API: POST, GET, PATCH, DELETE."""

    def __init__(self):
        self.zones: dict[str, dict] = {}

    def __call__(self, method, path, body=None):
        if path == "/zones" and method == "POST":
            name = body["name"]
            if name in self.zones:
                raise ValueError("Conflict")
            self.zones[name] = {"name": name, "rrsets": [dict(r, records=list(r["records"])) for r in body["rrsets"]]}
            return self.zones[name]
        match = re.fullmatch(r"/zones/(.+)", path)
        name = match.group(1)
        if name not in self.zones:
            raise LookupError("Could not find domain")
        zone = self.zones[name]
        if method == "GET":
            return json.loads(json.dumps(zone))
        if method == "DELETE":
            del self.zones[name]
            return None
        if method == "PATCH":
            for change in body["rrsets"]:
                zone["rrsets"] = [r for r in zone["rrsets"]
                                  if not (r["name"] == change["name"] and r["type"] == change["type"])]
                if change["changetype"] == "REPLACE":
                    zone["rrsets"].append({"name": change["name"], "type": change["type"], "ttl": change["ttl"],
                                           "records": change["records"]})
            return None
        raise AssertionError(method)

    def values(self, zone, name, rtype):
        for rrset in self.zones[zone + "."]["rrsets"]:
            if rrset["name"] == name + "." and rrset["type"] == rtype:
                return [r["content"] for r in rrset["records"]]
        return []


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("root_admin", "admin"), ("alice", "end_user"), ("bob", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    for owner, domain in (("alice", "alice.test"), ("bob", "bob.test"), ("alice", "shop.alice.test")):
        db.add(Website(domain=domain, owner_id=people[owner].id, root_path=f"/home/{owner}/{domain}",
                       document_root="public_html", linux_user=owner, php_version="8.3", app_type="php",
                       status="active"))
    db.commit()

    pdns = FakePowerDNS()
    monkeypatch.setattr(dns_manager, "_api", pdns)
    monkeypatch.setattr(dns_manager, "MARKER", tmp_path / "installed")
    (tmp_path / "installed").write_text("")
    monkeypatch.setattr(dns_manager, "server_addresses", lambda: {"ipv4": ["203.0.113.7"], "ipv6": ["2001:db8::7"]})
    monkeypatch.setattr(dns_manager, "_panel_host", lambda: "panel.example.net")
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

    try:
        yield SimpleNamespace(db=db, client=client, people=people, pdns=pdns, as_user=as_user, store=store)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def _zone(env, user, domain):
    res = env.as_user(user).post("/api/dns/zones", json={"domain": domain})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _records(env, user, zone_id):
    res = env.as_user(user).get(f"/api/dns/zones/{zone_id}")
    assert res.status_code == 200, res.text
    return res.json()["records"]


# ---------------------------------------------------------------------------
# zones
# ---------------------------------------------------------------------------
def test_a_new_zone_carries_soa_ns_and_the_websites_records(env):
    zone_id = _zone(env, "alice", "alice.test")
    assert env.pdns.values("alice.test", "alice.test", "NS") == ["ns1.panel.example.net.", "ns2.panel.example.net."]
    soa = env.pdns.values("alice.test", "alice.test", "SOA")[0].split()
    assert soa[0] == "ns1.panel.example.net." and soa[1] == "hostmaster.panel.example.net."
    assert env.pdns.values("alice.test", "alice.test", "A") == ["203.0.113.7"]
    assert env.pdns.values("alice.test", "www.alice.test", "AAAA") == ["2001:db8::7"]
    # The subdomain website of the same owner lands in the parent zone.
    assert env.pdns.values("alice.test", "shop.alice.test", "A") == ["203.0.113.7"]
    rows = _records(env, "alice", zone_id)
    assert all(r["locked"] for r in rows if r["type"] == "SOA" or (r["type"] == "NS" and r["name"] == "@"))


def test_customers_add_zones_only_for_their_own_websites(env):
    for domain in ("bob.test", "nobody.test"):
        assert env.as_user("alice").post("/api/dns/zones", json={"domain": domain}).status_code == 403
    assert env.as_user("root_admin").post("/api/dns/zones", json={"domain": "bob.test"}).status_code == 200
    assert env.db.query(DnsZone).filter_by(name="bob.test").one().owner_id == env.people["bob"].id


def test_customers_see_and_change_only_their_own_zones(env):
    own = _zone(env, "alice", "alice.test")
    other = _zone(env, "bob", "bob.test")
    assert [z["name"] for z in env.as_user("alice").get("/api/dns/zones").json()["items"]] == ["alice.test"]
    assert env.as_user("alice").get(f"/api/dns/zones/{other}").status_code == 404
    res = env.as_user("alice").post(f"/api/dns/zones/{other}/records", json={"name": "x", "type": "A", "value": "1.2.3.4"})
    assert res.status_code == 404
    assert env.as_user("root_admin").get(f"/api/dns/zones/{own}").status_code == 200


def test_every_domain_on_the_panel_gets_dns(env):
    alias = WebsiteAlias(website_id=env.db.query(Website).filter_by(domain="bob.test").one().id, domain="bob-alias.test")
    env.db.add(alias)
    env.db.commit()
    # A customer's sync covers their own domains only.
    res = env.as_user("alice").post("/api/dns/sync")
    assert res.status_code == 200, res.text
    assert res.json()["created"] == ["alice.test"]
    # The subdomain website lands in its domain's zone, not a zone of its own.
    assert env.pdns.values("alice.test", "shop.alice.test", "A") == ["203.0.113.7"]
    assert env.as_user("root_admin").post("/api/dns/sync").json()["created"] == ["bob-alias.test", "bob.test"]
    assert env.db.query(DnsZone).filter_by(name="bob-alias.test").one().owner_id == env.people["bob"].id
    assert env.as_user("root_admin").post("/api/dns/sync").json()["created"] == []
    names = [z["name"] for z in env.as_user("root_admin").get("/api/dns/zones").json()["items"]]
    assert names == ["alice.test", "bob-alias.test", "bob.test"]


def test_the_minute_tick_and_the_addon_install_make_the_zones():
    scheduler = (PROJECT_ROOT / "backend" / "app" / "services" / "backup_scheduler.py").read_text(encoding="utf-8")
    assert "dns_manager.tick()" in scheduler
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "addons.py").read_text(encoding="utf-8")
    assert 'if addon_id == "dns" and action == "install":' in source and "dns_manager.sync_zones(db)" in source


def test_a_zone_in_use_by_the_panel_is_not_deleted_by_hand(env):
    zone_id = _zone(env, "alice", "alice.test")
    assert env.as_user("alice").get("/api/dns/zones").json()["items"][0]["on_panel"] is True
    res = env.as_user("alice").delete(f"/api/dns/zones/{zone_id}?confirm=alice.test")
    assert res.status_code == 400 and "in use" in res.json()["detail"]
    # Once no website uses it, it can go -- by its name.
    for site in env.db.query(Website).filter(Website.domain.in_(["alice.test", "shop.alice.test"])).all():
        env.db.delete(site)
    env.db.commit()
    assert env.as_user("alice").get(f"/api/dns/zones/{zone_id}").json()["zone"]["on_panel"] is False
    assert env.as_user("alice").delete(f"/api/dns/zones/{zone_id}").status_code == 400
    assert env.as_user("alice").delete(f"/api/dns/zones/{zone_id}?confirm=alice.test").status_code == 200
    assert "alice.test." not in env.pdns.zones and env.db.query(DnsZone).count() == 0


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("record,content", [
    ({"name": "mail", "type": "A", "value": "198.51.100.4"}, "198.51.100.4"),
    ({"name": "@", "type": "MX", "value": "mx.example.net", "priority": 20}, "20 mx.example.net."),
    ({"name": "blog", "type": "CNAME", "value": "alice.test"}, "alice.test."),
    ({"name": "@", "type": "TXT", "value": "google-site-verification=abc"}, '"google-site-verification=abc"'),
    ({"name": "_sip._tcp", "type": "SRV", "value": "5 5060 sip.alice.test", "priority": 10}, "10 5 5060 sip.alice.test."),
    ({"name": "@", "type": "CAA", "value": "issue letsencrypt.org"}, '0 issue "letsencrypt.org"'),
    ({"name": "sub", "type": "NS", "value": "ns.other.example"}, "ns.other.example."),
])
def test_records_reach_powerdns_in_its_format(env, record, content):
    zone_id = _zone(env, "alice", "alice.test")
    res = env.as_user("alice").post(f"/api/dns/zones/{zone_id}/records", json={**record, "ttl": 300})
    assert res.status_code == 200, res.text
    name = "alice.test" if record["name"] == "@" else f"{record['name']}.alice.test"
    assert content in env.pdns.values("alice.test", name, record["type"])


def test_a_second_value_joins_the_rrset_and_edit_and_delete_touch_one_value(env):
    zone_id = _zone(env, "alice", "alice.test")
    url = f"/api/dns/zones/{zone_id}/records"
    env.as_user("alice").post(url, json={"name": "@", "type": "TXT", "value": "one"})
    env.as_user("alice").post(url, json={"name": "@", "type": "TXT", "value": "two"})
    assert env.pdns.values("alice.test", "alice.test", "TXT") == ['"one"', '"two"']
    res = env.as_user("alice").put(url, json={"old": {"name": "@", "type": "TXT", "content": '"one"'},
                                              "new": {"name": "@", "type": "TXT", "value": "uno"}})
    assert res.status_code == 200, res.text
    assert env.pdns.values("alice.test", "alice.test", "TXT") == ['"uno"', '"two"']
    res = env.as_user("alice").post(url + "/delete", json={"name": "@", "type": "TXT", "content": '"two"'})
    assert res.status_code == 200
    assert env.pdns.values("alice.test", "alice.test", "TXT") == ['"uno"']


@pytest.mark.parametrize("record", [
    {"name": "@", "type": "NS", "value": "evil.example"},
    {"name": "@", "type": "CNAME", "value": "x.example"},
    {"name": "x", "type": "A", "value": "not-an-ip"},
    {"name": "x", "type": "AAAA", "value": "1.2.3.4"},
    {"name": "a b", "type": "A", "value": "1.2.3.4"},
    {"name": "x", "type": "SOA", "value": "x"},
    {"name": "x", "type": "TXT", "value": "line\nbreak"},
    {"name": "x", "type": "SRV", "value": "5060 sip.example"},
    {"name": "x.other.test.", "type": "A", "value": "1.2.3.4"},
])
def test_bad_records_are_refused(env, record):
    zone_id = _zone(env, "alice", "alice.test")
    res = env.as_user("alice").post(f"/api/dns/zones/{zone_id}/records", json=record)
    assert res.status_code in (400, 422), record


def test_a_cname_stays_the_only_record_of_its_name(env):
    zone_id = _zone(env, "alice", "alice.test")
    url = f"/api/dns/zones/{zone_id}/records"
    # www already has the website's A and AAAA records.
    res = env.as_user("alice").post(url, json={"name": "www", "type": "CNAME", "value": "cdn.example.net"})
    assert res.status_code == 400 and "only record" in res.json()["detail"]
    env.as_user("alice").post(url, json={"name": "blog", "type": "CNAME", "value": "cdn.example.net"})
    res = env.as_user("alice").post(url, json={"name": "blog", "type": "CNAME", "value": "other.example.net"})
    assert res.status_code == 400 and "edit it instead" in res.json()["detail"]
    res = env.as_user("alice").post(url, json={"name": "blog", "type": "TXT", "value": "x"})
    assert res.status_code == 400 and "is a CNAME" in res.json()["detail"]
    assert env.pdns.values("alice.test", "blog.alice.test", "CNAME") == ["cdn.example.net."]


def test_the_apex_soa_and_ns_cannot_be_removed(env):
    zone_id = _zone(env, "alice", "alice.test")
    url = f"/api/dns/zones/{zone_id}/records/delete"
    ns = env.pdns.values("alice.test", "alice.test", "NS")[0]
    assert env.as_user("alice").post(url, json={"name": "@", "type": "NS", "content": ns}).status_code == 400
    soa = env.pdns.values("alice.test", "alice.test", "SOA")[0]
    assert env.as_user("alice").post(url, json={"name": "@", "type": "SOA", "content": soa}).status_code == 400


def test_a_long_txt_is_split_and_reads_back_whole(env):
    zone_id = _zone(env, "alice", "alice.test")
    value = "v=DKIM1; k=rsa; p=" + "A" * 400
    env.as_user("alice").post(f"/api/dns/zones/{zone_id}/records", json={"name": "k._domainkey", "type": "TXT", "value": value})
    stored = env.pdns.values("alice.test", "k._domainkey.alice.test", "TXT")[0]
    assert stored.count('" "') == 1
    row = next(r for r in _records(env, "alice", zone_id) if r["name"] == "k._domainkey")
    assert row["value"] == value


# ---------------------------------------------------------------------------
# the panel keeps records in step
# ---------------------------------------------------------------------------
def test_a_new_website_gets_a_zone_and_its_records(env):
    site = Website(domain="new.test", owner_id=env.people["alice"].id, root_path="/home/alice/new.test",
                   document_root="public_html", linux_user="alice", php_version="8.3", app_type="php", status="active")
    env.db.add(site)
    env.db.commit()
    dns_manager.website_created(env.db, site)
    assert env.db.query(DnsZone).filter_by(name="new.test").one().owner_id == env.people["alice"].id
    assert env.pdns.values("new.test", "www.new.test", "A") == ["203.0.113.7"]


def test_a_website_record_never_overwrites_what_the_owner_set(env):
    zone_id = _zone(env, "alice", "alice.test")
    for rtype, content in (("A", "203.0.113.7"), ("AAAA", "2001:db8::7")):
        env.as_user("alice").post(f"/api/dns/zones/{zone_id}/records/delete",
                                  json={"name": "www", "type": rtype, "content": content})
    res = env.as_user("alice").post(f"/api/dns/zones/{zone_id}/records", json={"name": "www", "type": "CNAME", "value": "cdn.example.net"})
    assert res.status_code == 200, res.text
    site = env.db.query(Website).filter_by(domain="alice.test").one()
    dns_manager.website_created(env.db, site)
    assert env.pdns.values("alice.test", "www.alice.test", "A") == []
    assert env.pdns.values("alice.test", "www.alice.test", "CNAME") == ["cdn.example.net."]


def test_a_removed_website_takes_its_own_records_with_it(env):
    _zone(env, "alice", "alice.test")
    env.pdns("PATCH", "/zones/alice.test.", {"rrsets": [{"name": "www.shop.alice.test.", "type": "A", "ttl": 300,
                                                          "changetype": "REPLACE",
                                                          "records": [{"content": "203.0.113.7", "disabled": False},
                                                                      {"content": "198.51.100.9", "disabled": False}]}]})
    shop = env.db.query(Website).filter_by(domain="shop.alice.test").one()
    env.db.delete(shop)
    env.db.commit()
    dns_manager.website_removed(env.db, ["shop.alice.test"], env.people["alice"].id)
    assert env.pdns.values("alice.test", "shop.alice.test", "A") == []
    assert env.pdns.values("alice.test", "shop.alice.test", "AAAA") == []
    # The value the owner added stays; the zone and the other website too.
    assert env.pdns.values("alice.test", "www.shop.alice.test", "A") == ["198.51.100.9"]
    assert env.pdns.values("alice.test", "alice.test", "A") == ["203.0.113.7"]
    # A name another website still serves keeps its records.
    dns_manager.website_removed(env.db, ["alice.test"], env.people["alice"].id)
    assert env.pdns.values("alice.test", "alice.test", "A") == ["203.0.113.7"]
    source = (PROJECT_ROOT / "backend" / "app" / "api" / "websites.py").read_text(encoding="utf-8")
    assert source.count("dns_manager.website_removed(") == 2


def test_no_zone_is_made_in_another_accounts_parent_zone(env):
    _zone(env, "root_admin", "bob.test")
    site = Website(domain="shop.bob.test", owner_id=env.people["alice"].id, root_path="/x", document_root="public_html",
                   linux_user="alice", php_version="8.3", app_type="php", status="active")
    env.db.add(site)
    env.db.commit()
    dns_manager.website_created(env.db, site)
    assert env.pdns.values("bob.test", "shop.bob.test", "A") == []


def test_mail_records_are_written_into_the_zone(env, monkeypatch):
    _zone(env, "alice", "alice.test")
    env.pdns("PATCH", "/zones/alice.test.", {"rrsets": [{"name": "alice.test.", "type": "TXT", "ttl": 300,
                                                          "changetype": "REPLACE",
                                                          "records": [{"content": '"keep-me"', "disabled": False},
                                                                      {"content": '"v=spf1 -all"', "disabled": False}]}]})
    row = MailDomain(domain="alice.test", owner_id=env.people["alice"].id, dkim_public="A" * 392, catch_all="",
                     webmail_host=False, relay="", dns_custom="")
    env.db.add(row)
    env.db.commit()
    monkeypatch.setattr(mail, "hostname", lambda: "panel.example.net")
    monkeypatch.setattr(mail.network, "detect_addresses", lambda: {"ipv4": ["203.0.113.7"], "ipv6": []})
    dns_manager.mail_domain_changed(env.db, [row])
    assert env.pdns.values("alice.test", "alice.test", "MX") == ["10 panel.example.net."]
    txt = env.pdns.values("alice.test", "alice.test", "TXT")
    assert '"keep-me"' in txt and '"v=spf1 -all"' not in txt
    assert any(v.startswith('"v=spf1 mx a ip4:203.0.113.7') for v in txt)
    dkim = env.pdns.values("alice.test", "opanel._domainkey.alice.test", "TXT")[0]
    assert dns_manager._untxt(dkim) == "v=DKIM1; k=rsa; p=" + "A" * 392
    assert dns_manager._untxt(env.pdns.values("alice.test", "_dmarc.alice.test", "TXT")[0]).startswith("v=DMARC1")
    dns_manager.mail_domain_removed(env.db, "alice.test", env.people["alice"].id, "panel.example.net")
    assert env.pdns.values("alice.test", "alice.test", "MX") == []
    assert env.pdns.values("alice.test", "opanel._domainkey.alice.test", "TXT") == []


# ---------------------------------------------------------------------------
# Email and DNS Manager agree over time
# ---------------------------------------------------------------------------
BREVO = {"id": "brevo", "name": "Brevo", "host": "smtp-relay.brevo.com", "port": 587,
         "spf_include": "include:spf.brevo.com",
         "dns_records": [{"type": "TXT", "name": "@", "value": "brevo-code:abc"},
                         {"type": "CNAME", "name": "brevo1._domainkey", "value": "b1.{domain}.dkim.brevo.com"}]}


@pytest.fixture
def mailzone(env, monkeypatch):
    """alice.test: a zone and a mail domain, its records written once."""
    monkeypatch.setattr(mail, "hostname", lambda: "panel.example.net")
    monkeypatch.setattr(mail.network, "detect_addresses", lambda: {"ipv4": ["203.0.113.7"], "ipv6": []})
    env.store["mail"] = {"settings": {"relays": [dict(BREVO)]}}
    zone_id = _zone(env, "alice", "alice.test")
    row = MailDomain(domain="alice.test", owner_id=env.people["alice"].id, dkim_public="A" * 392, catch_all="",
                     webmail_host=False, relay="", dns_custom="")
    env.db.add(row)
    env.db.commit()
    dns_manager.mail_domain_changed(env.db, [row])

    def change(**fields):
        for key, value in fields.items():
            setattr(row, key, value)
        env.db.commit()
        dns_manager.mail_domain_changed(env.db, [row])

    def txt(name="alice.test"):
        return sorted(dns_manager._untxt(v) for v in env.pdns.values("alice.test", name, "TXT"))

    return SimpleNamespace(row=row, zone_id=zone_id, change=change, txt=txt)


def _owner_record(env, zone_id, record):
    res = env.as_user("alice").post(f"/api/dns/zones/{zone_id}/records", json=record)
    assert res.status_code == 200, res.text


def test_another_relay_takes_the_old_relays_records_away(env, mailzone):
    _owner_record(env, mailzone.zone_id, {"name": "@", "type": "TXT", "value": "google-site-verification=keep"})
    mailzone.change(relay="brevo")
    assert "brevo-code:abc" in mailzone.txt()
    assert "include:spf.brevo.com" in next(t for t in mailzone.txt() if t.startswith("v=spf1"))
    assert env.pdns.values("alice.test", "brevo1._domainkey.alice.test", "CNAME") == ["b1.alice.test.dkim.brevo.com."]
    mailzone.change(relay="direct")
    assert mailzone.txt() == ["google-site-verification=keep", "v=spf1 mx a ip4:203.0.113.7 ~all"]
    assert env.pdns.values("alice.test", "brevo1._domainkey.alice.test", "CNAME") == []


def test_an_extra_record_the_admin_removes_leaves_the_zone(env, mailzone):
    mailzone.change(dns_custom=json.dumps({"records": [{"type": "TXT", "name": "@", "value": "site-verify=xyz"}]}))
    assert "site-verify=xyz" in mailzone.txt()
    mailzone.change(dns_custom="")
    assert "site-verify=xyz" not in mailzone.txt()


def test_what_the_owner_adds_to_the_spf_survives_and_the_old_relay_does_not(env, mailzone):
    mailzone.change(relay="brevo")
    spf = next(r for r in _records(env, "alice", mailzone.zone_id) if r["type"] == "TXT" and r["value"].startswith("v=spf1"))
    res = env.as_user("alice").put(f"/api/dns/zones/{mailzone.zone_id}/records", json={
        "old": {"name": "@", "type": "TXT", "content": spf["content"]},
        "new": {"name": "@", "type": "TXT", "value": spf["value"].replace(" ~all", " include:_spf.google.com -all")}})
    assert res.status_code == 200, res.text
    mailzone.change(relay="direct")
    assert [t for t in mailzone.txt() if t.startswith("v=spf1")] == ["v=spf1 mx a ip4:203.0.113.7 include:_spf.google.com -all"]


def test_the_owners_own_dmarc_policy_stays(env, mailzone):
    records = _records(env, "alice", mailzone.zone_id)
    dmarc = next(r for r in records if r["name"] == "_dmarc")
    res = env.as_user("alice").put(f"/api/dns/zones/{mailzone.zone_id}/records", json={
        "old": {"name": "_dmarc", "type": "TXT", "content": dmarc["content"]},
        "new": {"name": "_dmarc", "type": "TXT", "value": "v=DMARC1; p=reject"}})
    assert res.status_code == 200, res.text
    mailzone.change(relay="brevo")
    assert mailzone.txt("_dmarc.alice.test") == ["v=DMARC1; p=reject"]
    # An administrator's own DMARC for the domain does replace it.
    mailzone.change(dns_custom=json.dumps({"dmarc": "v=DMARC1; p=none"}))
    assert mailzone.txt("_dmarc.alice.test") == ["v=DMARC1; p=none"]


def test_a_new_mail_host_replaces_the_old_mx_and_keeps_the_owners(env, mailzone, monkeypatch):
    _owner_record(env, mailzone.zone_id, {"name": "@", "type": "MX", "value": "backup-mx.example.org", "priority": 50})
    monkeypatch.setattr(mail, "hostname", lambda: "mail.example.net")
    mailzone.change()
    assert sorted(env.pdns.values("alice.test", "alice.test", "MX")) == ["10 mail.example.net.", "50 backup-mx.example.org."]


def test_webmail_address_the_owner_set_is_not_overwritten(env, mailzone):
    records = _records(env, "alice", mailzone.zone_id)
    webmail = next(r for r in records if r["name"] == "webmail")
    env.as_user("alice").post(f"/api/dns/zones/{mailzone.zone_id}/records/delete",
                              json={"name": "webmail", "type": "A", "content": webmail["content"]})
    _owner_record(env, mailzone.zone_id, {"name": "webmail", "type": "A", "value": "198.51.100.20"})
    mailzone.change(relay="brevo")
    assert env.pdns.values("alice.test", "webmail.alice.test", "A") == ["198.51.100.20"]


def test_turning_email_off_takes_back_what_the_panel_wrote(env, mailzone):
    mailzone.change(relay="brevo")
    _owner_record(env, mailzone.zone_id, {"name": "@", "type": "TXT", "value": "google-site-verification=keep"})
    dns_manager.mail_domain_removed(env.db, "alice.test", env.people["alice"].id, "panel.example.net")
    assert mailzone.txt() == ["google-site-verification=keep"]
    for name, rtype in (("alice.test", "MX"), ("opanel._domainkey.alice.test", "TXT"), ("_dmarc.alice.test", "TXT"),
                        ("webmail.alice.test", "A"), ("brevo1._domainkey.alice.test", "CNAME")):
        assert env.pdns.values("alice.test", name, rtype) == [], (name, rtype)
    assert env.pdns.values("alice.test", "alice.test", "A") == ["203.0.113.7"]  # the website's
    assert dns_manager._managed(env.db.get(DnsZone, mailzone.zone_id)) == {}


def test_the_email_page_reads_the_zone_the_writer_uses(env, mailzone):
    res = env.as_user("alice").get(f"/api/mail/domains/{mailzone.row.id}/dns")
    assert res.status_code == 200, res.text
    view = res.json()
    assert view["dns_zone"] == {"id": mailzone.zone_id, "name": "alice.test"}
    assert {r["key"]: r["in_zone"] for r in view["records"]} == {
        "mx": True, "spf": True, "dkim": True, "dmarc": True, "webmail": True}
    # Records the Email addon keeps are marked on the zone page.
    marked = {(r["name"], r["type"]) for r in _records(env, "alice", mailzone.zone_id) if r["mail"] == "alice.test"}
    assert {("@", "MX"), ("@", "TXT"), ("opanel._domainkey", "TXT"), ("_dmarc", "TXT"), ("webmail", "A")} <= marked
    assert ("@", "A") not in marked


def test_a_zone_of_another_account_is_neither_written_nor_claimed(env, monkeypatch):
    monkeypatch.setattr(mail, "hostname", lambda: "panel.example.net")
    monkeypatch.setattr(mail.network, "detect_addresses", lambda: {"ipv4": ["203.0.113.7"], "ipv6": []})
    res = env.as_user("root_admin").post("/api/dns/zones", json={"domain": "alice.test", "owner_id": env.people["bob"].id})
    assert res.status_code == 200, res.text
    row = MailDomain(domain="alice.test", owner_id=env.people["alice"].id, dkim_public="A" * 392, catch_all="",
                     webmail_host=False, relay="", dns_custom="")
    env.db.add(row)
    env.db.commit()
    dns_manager.mail_domain_changed(env.db, [row])
    assert env.pdns.values("alice.test", "alice.test", "MX") == []
    assert env.as_user("root_admin").get(f"/api/mail/domains/{row.id}/dns").json()["dns_zone"] is None


def test_hooks_do_nothing_without_the_addon(env):
    dns_manager.MARKER.unlink()
    site = env.db.query(Website).filter_by(domain="alice.test").one()
    dns_manager.website_created(env.db, site)
    assert env.pdns.zones == {}
    assert env.as_user("alice").post("/api/dns/zones", json={"domain": "alice.test"}).status_code == 409


def test_nameserver_changes_reach_every_zone(env):
    _zone(env, "alice", "alice.test")
    res = env.as_user("root_admin").put("/api/dns/settings", json={"ns1": "ns1.bnix.vn", "ns2": "ns2.bnix.vn"})
    assert res.status_code == 200, res.text
    assert env.pdns.values("alice.test", "alice.test", "NS") == ["ns1.bnix.vn.", "ns2.bnix.vn."]
    assert env.pdns.values("alice.test", "alice.test", "SOA")[0].startswith("ns1.bnix.vn. ")
    assert env.as_user("alice").put("/api/dns/settings", json={"ns1": "x.example"}).status_code == 403


def test_glue_for_nameservers_inside_a_hosted_zone(env):
    env.as_user("root_admin").put("/api/dns/settings", json={"ns1": "ns1.alice.test", "ns2": "ns2.alice.test"})
    _zone(env, "alice", "alice.test")
    assert env.pdns.values("alice.test", "ns1.alice.test", "A") == ["203.0.113.7"]


def test_deleting_an_account_takes_its_zones(env):
    _zone(env, "alice", "alice.test")
    alice = env.db.get(User, env.people["alice"].id)
    assert dns_manager.delete_for_owner(env.db, alice) == ["alice.test"]
    env.db.commit()
    assert env.pdns.zones == {}
    source = (PROJECT_ROOT / "backend" / "app" / "api" / "users.py").read_text(encoding="utf-8")
    assert "dns_manager.delete_for_owner(db, user)" in source


def test_wildcard_ssl_over_local_dns_needs_a_hosted_zone(env, monkeypatch):
    from app.services import ssl

    site = env.db.query(Website).filter_by(domain="alice.test").one()
    calls = []
    monkeypatch.setattr(ssl, "issue_wildcard_local", lambda domain, email=None: calls.append(domain) or SimpleNamespace(returncode=1, stdout="", stderr="stop here"))
    res = env.as_user("alice").post(f"/api/websites/{site.id}/ssl/wildcard", json={"provider": "opanel"})
    assert res.status_code == 400 and "no zone" in res.json()["detail"]
    _zone(env, "alice", "alice.test")
    import app.api.websites as websites_api
    monkeypatch.setattr(websites_api, "_rewrite_website_vhost", lambda *a, **k: "")
    res = env.as_user("alice").post(f"/api/websites/{site.id}/ssl/wildcard", json={"provider": "opanel"})
    assert calls == ["alice.test"]


# ---------------------------------------------------------------------------
# the helper
# ---------------------------------------------------------------------------
def test_the_helper_installs_nothing_until_the_addon_is():
    for script in ("install.sh", "update.sh"):
        text = (PROJECT_ROOT / "installer" / script).read_text(encoding="utf-8")
        for word in ("pdns", "powerdns", "dns_refresh"):
            assert word not in text.lower(), (script, word)
    refresh = HELPER[HELPER.index("dns_refresh() {"):]
    assert refresh.index("dns_installed || return 0") < refresh.index("apt-get")
    assert HELPER.count("if dns_installed; then dns_open_ports; fi") == 2
    assert "( dns_refresh ) ||" in HELPER


def test_powerdns_listens_on_real_addresses_and_its_api_on_loopback():
    assert "webserver-address=127.0.0.1" in HELPER and "webserver-allow-from=127.0.0.1,::1" in HELPER
    assert "local-address=${addresses}" in HELPER and "disable-axfr=yes" in HELPER
    assert 'for pkg in bind9 dnsmasq; do' in HELPER


def test_the_database_is_made_from_the_full_schema_and_never_replaced():
    init = HELPER[HELPER.index("dns_init_db() {"):]
    init = init[:init.index("\n}\n")]
    # 3.4.0_to_4.0.0_schema.sqlite3.sql sorts first and is only an upgrade script.
    assert r"grep -E '/schema\.sqlite3\.sql(\.gz)?$'" in init
    assert 'if [[ ! -s "$DNS_DB" ]]; then' in init
    assert 'mv -f "${DNS_DB}.new" "$DNS_DB"' in init


def test_the_acme_hook_adds_and_removes_one_value():
    hook = HELPER[HELPER.index("dns_write_acme_hook() {"):]
    hook = hook[:hook.index("\nHOOK\n")]
    assert 'values = [v for v in current if v != value] + ([value] if action == "auth" else [])' in hook
    assert 'changetype="DELETE"' in hook
