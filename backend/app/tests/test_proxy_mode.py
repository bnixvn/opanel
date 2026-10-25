"""Website mode "proxy": OpenLiteSpeed in front of any http(s) address
(operator, 2026-10-25). Tried live on .41: the path is joined, the visitor's
Host kept or the upstream's sent, the ACME challenge served from disk, an
https upstream spoken to in TLS, WebSocket upgrades routed straight."""
import pytest

from app.services import openlitespeed


def _vhost(url, **kwargs):
    return openlitespeed.render_vhost("example.test", "/home/u/example.test", app_type="proxy",
                                      php_version=None, proxy_url=url, **kwargs)


def test_every_request_but_the_challenge_goes_to_the_upstream():
    rendered = _vhost("http://127.0.0.1:8080")
    assert "# OPANEL PROXY TARGET: http://127.0.0.1:8080" in rendered
    assert "type                  proxy" in rendered and "address               127.0.0.1:8080" in rendered
    assert "RewriteCond %{REQUEST_URI} !^/\\.well-known/acme-challenge/" in rendered
    assert "RewriteRule ^/?(.*)$ http://opanel_proxy_example_test/$1 [P,L]" in rendered
    assert "websocket / {" in rendered
    # No PHP at all.
    assert "lsapi" not in rendered


def test_an_https_upstream_with_a_path_and_its_own_host():
    rendered = _vhost("https://backend.example.net/app/", proxy_preserve_host=False)
    assert "address               https://backend.example.net:443" in rendered
    assert "RewriteRule ^/?(.*)$ http://opanel_proxy_example_test/app/$1 [P,E=Proxy-Host:backend.example.net,L]" in rendered
    assert "# OPANEL PROXY HOST: upstream" in rendered
    # The visitor's path would skip /app/, so no WebSocket route.
    assert "websocket /" not in rendered


@pytest.mark.parametrize("url", ["", "ftp://x.net", "http://x.net; return", "http://x.net/$1", "http://x.net/%2e", "http://a b"])
def test_nothing_litespeed_would_read_as_syntax(url):
    with pytest.raises(ValueError):
        openlitespeed.validate_proxy_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:3000", "http://localhost", "http://10.1.2.3", "http://[::1]:80"])
def test_a_customer_cannot_point_a_site_inside_the_server(url):
    with pytest.raises(ValueError, match="administrator"):
        openlitespeed.validate_proxy_url(url, allow_private=False)
    assert openlitespeed.validate_proxy_url(url) == url


def test_a_rewrite_from_disk_keeps_the_proxy(monkeypatch):
    rendered = _vhost("http://127.0.0.1:8080", proxy_preserve_host=False)
    seen = {}
    monkeypatch.setattr(openlitespeed, "read_vhost_config", lambda domain: rendered)
    monkeypatch.setattr(openlitespeed, "rewrite_vhost", lambda domain, root, **kw: seen.update(kw) or "")
    openlitespeed._rewrite_existing_vhost("example.test", waf_enabled=True)
    assert seen["app_type"] == "proxy"
    assert seen["proxy_url"] == "http://127.0.0.1:8080" and seen["proxy_preserve_host"] is False
