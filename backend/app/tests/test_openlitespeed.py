from app.services import openlitespeed


def test_rewrite_vhost_ignores_nginx_compat_ssl_kwargs(monkeypatch):
    captured = {}

    def fake_render_vhost(domain, root_path, **kwargs):
        captured["domain"] = domain
        captured["root_path"] = root_path
        captured["kwargs"] = kwargs
        return "vhssl  { }"

    class DummyShell:
        def privileged(self, *args, **kwargs):
            captured["helper"] = args
            captured["helper_kwargs"] = kwargs

    monkeypatch.setattr(openlitespeed, "render_vhost", fake_render_vhost)
    monkeypatch.setattr(openlitespeed, "shell", DummyShell())

    result = openlitespeed.rewrite_vhost(
        "example.test",
        "/home/admin/example.test",
        app_type="wordpress",
        php_version="8.4",
        include_ssl=False,
        preserve_existing_ssl=False,
    )

    assert result == "vhssl  { }"
    assert "include_ssl" not in captured["kwargs"]
    assert "preserve_existing_ssl" not in captured["kwargs"]


def test_wordpress_vhost_runs_lsphp_as_site_user():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="wordpress",
        php_version="8.4",
        linux_user="siteuser",
        lsphp_socket_override="/tmp/lshttpd/example.sock",
    )

    assert "extUser               siteuser" in rendered
    assert "extGroup              siteuser" in rendered


def test_wordpress_vhost_blocks_xmlrpc_before_php():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="wordpress",
        php_version="8.4",
    )

    assert "RewriteRule ^xmlrpc\\.php$ - [F,L]" in rendered


def test_wordpress_vhost_includes_security_headers_when_ssl_is_enabled():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="wordpress",
        php_version="8.4",
        linux_user="siteuser",
        ssl_cert_path="/etc/letsencrypt/live/example.test/fullchain.pem",
        ssl_key_path="/etc/letsencrypt/live/example.test/privkey.pem",
    )

    assert "Strict-Transport-Security: max-age=31536000; includeSubDomains" in rendered
    assert "X-Frame-Options: SAMEORIGIN" in rendered
    assert "X-Content-Type-Options: nosniff" in rendered
    assert "Referrer-Policy: strict-origin-when-cross-origin" in rendered
    assert "Permissions-Policy: accelerometer=(), autoplay=(), camera=()" in rendered
    assert "Content-Security-Policy:" in rendered


def test_static_vhost_does_not_emit_hsts_without_ssl():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="static",
        linux_user="siteuser",
    )

    assert "Strict-Transport-Security:" not in rendered
    assert "X-Frame-Options: SAMEORIGIN" in rendered
    assert "X-Content-Type-Options: nosniff" in rendered
    assert "Referrer-Policy: strict-origin-when-cross-origin" in rendered
    assert "Permissions-Policy: accelerometer=(), autoplay=(), camera=()" in rendered


def test_vhost_uses_waf_site_rules_path():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="wordpress",
        php_version="8.4",
        waf_enabled=True,
    )

    assert "modsecurity_rules_file  /usr/local/lsws/conf/opanel/waf/sites/example.test.conf" in rendered


def test_vhost_ignores_custom_directives():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        custom_directives="context /danger { type static }",
    )

    assert "context /danger" not in rendered


def test_http_flood_uses_dedicated_block_not_app_rewrite():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        rewrite_mode="front_controller",
        http_flood_enabled=True,
        http_flood_config={"access_limit_requests": 20, "access_limit_window": 10, "access_limit_burst": 5, "connection_limit": 9},
    )

    assert "# OPANEL HTTP FLOOD BEGIN" in rendered
    assert "maxConns                9" in rendered


def test_update_waf_block_rerenders_existing_vhost_without_custom_directives(monkeypatch):
    existing = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        aliases=["alias.test"],
        redirects=[{"source": "old.test", "target": "https://example.test", "code": 301}],
    )
    captured = {}

    monkeypatch.setattr(openlitespeed, "read_vhost_config", lambda domain: existing)

    def fake_rewrite_vhost(domain, root_path, **kwargs):
        captured["domain"] = domain
        captured["root_path"] = root_path
        captured["kwargs"] = kwargs
        return "rewritten"

    monkeypatch.setattr(openlitespeed, "rewrite_vhost", fake_rewrite_vhost)

    assert openlitespeed.update_waf_block("example.test", True) == "rewritten"
    assert captured["domain"] == "example.test"
    assert captured["root_path"] == "/home/siteuser/example.test"
    assert captured["kwargs"]["waf_enabled"] is True
    assert captured["kwargs"]["custom_directives"] == ""
    assert captured["kwargs"]["aliases"] == ["alias.test"]
    assert captured["kwargs"]["redirects"] == [{"source": "old.test", "target": "https://example.test", "code": 301}]
