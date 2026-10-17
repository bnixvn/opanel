"""The DNS zone template and the address override (as SNPanel's DNS settings, 2026-10-17)."""
import pytest

from app.services import dns_manager


@pytest.fixture
def settings(monkeypatch):
    values = dict(dns_manager.SETTINGS_DEFAULTS, ns1="ns1.panel.example", ns2="ns2.panel.example",
                  hostmaster="hostmaster@panel.example")
    monkeypatch.setattr(dns_manager, "current_settings", lambda: values)
    monkeypatch.setattr(dns_manager.network, "detect_addresses", lambda: {"ipv4": ["198.51.100.7"], "ipv6": []})
    return values


def test_the_template_is_checked_record_by_record():
    clean = dns_manager.check_template([
        {"name": "@", "type": "txt", "value": "v=spf1 a mx ip4:{ip} ~all"},
        {"name": "@", "type": "CAA", "value": "issue letsencrypt.org"},
        {"name": "_dmarc", "type": "TXT", "value": "v=DMARC1; p=none"},
        {"name": "@", "type": "MX", "value": "mail.{domain}", "priority": 10},
    ])
    assert [entry["type"] for entry in clean] == ["TXT", "CAA", "TXT", "MX"] and clean[3]["priority"] == 10
    for bad in ([{"name": "@", "type": "NS", "value": "x.example"}], [{"name": "a b", "type": "A", "value": "{ip}"}],
                [{"name": "@", "type": "A", "value": "not-an-ip"}], [{"name": "@", "type": "TXT", "value": ""}],
                [{"name": "@", "type": "TXT", "value": "x"}] * 31):
        with pytest.raises(ValueError):
            dns_manager.check_template(bad)


def test_an_override_wins_over_the_detected_address(settings):
    assert dns_manager.server_addresses()["ipv4"] == ["198.51.100.7"]
    settings["ipv4"] = "203.0.113.9"
    assert dns_manager.server_addresses()["ipv4"] == ["203.0.113.9"]


def test_a_new_zone_gets_the_template_where_it_has_nothing_of_that_kind(settings, monkeypatch):
    settings["zone_template"] = [
        {"name": "@", "type": "TXT", "value": "v=spf1 a mx ip4:{ip} ~all", "priority": None},
        {"name": "@", "type": "CAA", "value": "issue letsencrypt.org", "priority": None},
        {"name": "www", "type": "CNAME", "value": "{domain}", "priority": None},
        {"name": "v6", "type": "AAAA", "value": "{ipv6}", "priority": None},
    ]
    zone_data = {"rrsets": [{"name": "shop.example.", "type": "TXT", "records": [{"content": '"google-site"'}]},
                            {"name": "www.shop.example.", "type": "A", "records": [{"content": "198.51.100.7"}]}]}
    monkeypatch.setattr(dns_manager, "_get_zone", lambda zone: zone_data)
    sent = []
    monkeypatch.setattr(dns_manager, "_patch", lambda zone, rrsets: sent.extend(rrsets))

    dns_manager._apply_template("shop.example")

    written = {(rrset["name"], rrset["type"]): [r["content"] for r in rrset["records"]] for rrset in sent}
    # The TXT already there stays; www has an A, so no CNAME; no IPv6 here, so no AAAA.
    assert written == {("shop.example.", "CAA"): ['0 issue "letsencrypt.org"']}
