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
