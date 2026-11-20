"""An uploaded certificate is served with its CA chain on OpenLiteSpeed 1.9.4.

The vhost templates named the chain with caBundlePath, which is no OLS setting:
1.9.4 refuses it ("Not support [cabundlepath]"), so after the 1.9.4 upgrade
every site with an uploaded certificate and a CA bundle sent its leaf alone,
and clients that do not fetch the intermediate themselves refused it
(production box, 2026-11-20). OLS sends what certFile holds (certChain 1), so
certFile is the fullchain.crt the helper writes next to cert.crt.
"""
from pathlib import Path

import pytest

from app.services import openlitespeed as ols

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = PROJECT_ROOT / "backend" / "app" / "templates" / "openlitespeed"
BASE = "/usr/local/lsws/conf/opanel/ssl/sites/example.test"


@pytest.mark.parametrize("name", ["php.conf.j2", "proxy.conf.j2", "static.conf.j2", "wordpress.conf.j2"])
def test_no_template_names_the_unsupported_setting(name):
    text = (TEMPLATES / name).read_text(encoding="utf-8").lower()
    assert "cabundlepath" not in text
    assert "certchain             1" in text


def test_with_a_ca_bundle_the_fullchain_is_served(monkeypatch):
    monkeypatch.setattr(ols.os.path, "isfile", lambda path: path == f"{BASE}/fullchain.crt")
    assert ols._chained_certificate(f"{BASE}/cert.crt", f"{BASE}/ca.crt") == f"{BASE}/fullchain.crt"


def test_a_missing_fullchain_never_takes_the_site_down(monkeypatch):
    monkeypatch.setattr(ols.os.path, "isfile", lambda path: False)
    assert ols._chained_certificate(f"{BASE}/cert.crt", f"{BASE}/ca.crt") == f"{BASE}/cert.crt"


@pytest.mark.parametrize("cert, ca", [
    (f"{BASE}/cert.crt", None),                                           # no bundle: the leaf is all there is
    ("/etc/letsencrypt/live/example.test/fullchain.pem", None),           # Let's Encrypt is a chain already
    (f"{BASE}/fullchain.crt", f"{BASE}/ca.crt"),                          # already the chain
    (None, None),
])
def test_everything_else_is_left_as_it_is(cert, ca):
    assert ols._chained_certificate(cert, ca) == cert


def test_update_writes_a_missing_fullchain_before_the_site_refresh():
    update = (PROJECT_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    call = update.index("ensure_manual_cert_fullchains || true")
    assert call < update.index("Refreshing managed site config")
    body = update.split("ensure_manual_cert_fullchains() {", 1)[1].split("\n}\n", 1)[0]
    assert 'cat "$dir/cert.crt" "$dir/ca.crt"' in body and "fullchain.crt" in body
