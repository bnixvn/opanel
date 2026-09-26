from pathlib import Path

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


def _capture_rewrite(monkeypatch, **extra):
    captured = {}
    monkeypatch.setattr(openlitespeed, "render_vhost", lambda *a, **k: "vh { }")

    class DummyShell:
        def privileged(self, *args, **kwargs):
            captured["subcommand"] = args[0]
            captured["kwargs"] = kwargs

    monkeypatch.setattr(openlitespeed, "shell", DummyShell())
    openlitespeed.rewrite_vhost("example.test", "/home/admin/example.test", **extra)
    return captured


def test_rewrite_vhost_reloads_ols_by_default(monkeypatch):
    captured = _capture_rewrite(monkeypatch)
    assert captured["subcommand"] == "ols-vhost-write"
    assert "defer_reload" not in captured["kwargs"]


def test_rewrite_vhost_defer_reload_stages_only(monkeypatch):
    captured = _capture_rewrite(monkeypatch, defer_reload=True)
    assert captured["subcommand"] == "ols-vhost-write-defer"
    # the fallback for the deferred variant must not restart the web server
    fallback = " ".join(captured["kwargs"]["fallback"])
    assert "lshttpd" not in fallback and "lswsctrl" not in fallback


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


def test_php_error_log_goes_to_a_dir_the_site_user_owns():
    """PHP runs as the site's Linux user, which cannot write the OLS-owned
    <domain>.error.log -- so php's error_log must point at a per-domain dir."""
    rendered = openlitespeed.render_vhost(
        "example.test", "/home/siteuser/example.test",
        app_type="wordpress", php_version="8.4", linux_user="siteuser",
    )
    assert "php_admin_value   error_log /var/log/opanel-php/example.test/php_error.log" in rendered
    # the OLS server error log is unchanged
    assert "errorlog /var/log/openlitespeed/example.test.error.log {" in rendered


def test_read_site_log_error_merges_php_and_server_logs(monkeypatch):
    captured = {}

    class _R:
        returncode = 0
        stdout = "php lines\n"
        stderr = ""

    def fake_privileged(cmd, helper_args=None, **kw):
        captured["cmd"] = cmd
        captured["args"] = helper_args
        captured["fallback"] = kw.get("fallback")
        return _R()

    monkeypatch.setattr(openlitespeed.shell, "privileged", fake_privileged)
    out = openlitespeed.read_site_log("example.test", "error", 100)
    assert captured["args"] == ["example.test", "error", "100"]
    assert "example.test/php_error.log" in out["path"]
    assert "example.test.error.log" in out["path"]
    # the un-privileged fallback tails both files too
    assert "php_error.log" in " ".join(captured["fallback"])


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
    assert "X-Frame-Options" not in rendered
    assert "X-Content-Type-Options: nosniff" in rendered
    assert "Referrer-Policy: strict-origin-when-cross-origin" in rendered
    assert "Permissions-Policy" not in rendered
    assert "Content-Security-Policy:" in rendered


def test_static_vhost_does_not_emit_hsts_without_ssl():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="static",
        linux_user="siteuser",
    )

    assert "Strict-Transport-Security:" not in rendered
    assert "X-Frame-Options" not in rendered
    assert "X-Content-Type-Options: nosniff" in rendered
    assert "Referrer-Policy: strict-origin-when-cross-origin" in rendered
    assert "Permissions-Policy" not in rendered


def test_vhost_uses_waf_site_rules_path():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="wordpress",
        php_version="8.4",
        waf_enabled=True,
    )

    assert "modsecurity_rules_file  /usr/local/lsws/conf/opanel/waf/sites/example.test.conf" in rendered
    assert "modsecurity           on" in rendered
    assert "modsecurity           1" not in rendered


def test_vhost_ignores_custom_directives():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        custom_directives="context /danger { type static }",
    )

    assert "context /danger" not in rendered


def test_http_flood_is_gone_from_rendered_vhosts():
    # It only ever emitted an extprocessor nothing referenced. Keep it out.
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        rewrite_mode="front_controller",
    )

    assert "HTTP FLOOD" not in rendered
    assert "extprocessor opanel_hf_" not in rendered
    assert not hasattr(openlitespeed, "update_http_flood_block")
    assert not hasattr(openlitespeed, "sync_http_flood_zones")


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
    # Redirect sources also appear in vhAliases so LiteSpeed routes them here;
    # they must not come back as plain aliases or the redirect would stop.
    assert captured["kwargs"]["aliases"] == ["alias.test"]
    assert captured["kwargs"]["redirects"] == [
        {"host": "old.test", "source": "old.test", "target": "https://example.test", "code": 301}
    ]


def _context_block(rendered: str) -> str:
    return rendered.split("context / {", 1)[1]


def _vhost_scope(rendered: str) -> str:
    return rendered.split("context / {", 1)[0]


def test_laravel_front_controller_rules_live_at_vhost_scope_not_in_the_context():
    """A nested document root breaks %{REQUEST_FILENAME} inside `context /`.

    With the rules in the context, !-f never matched, so every request -- real
    static assets included -- was rewritten to index.php and Laravel 404'd them.
    """
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        rewrite_mode="laravel",
    )
    assert "docRoot                   /home/siteuser/example.test/public_html/public" in rendered
    assert "REQUEST_FILENAME" not in _context_block(rendered)
    assert "RewriteRule ^(.*)$ /index.php [QSA,L]" in _vhost_scope(rendered)


def test_laravel_without_ssl_still_gets_a_vhost_rewrite_block():
    """The vhost rewrite block used to be emitted only when SSL was on."""
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        rewrite_mode="laravel",
        ssl_enabled=False,
    )
    assert "RewriteRule ^(.*)$ /index.php [QSA,L]" in _vhost_scope(rendered)


def test_a_flat_document_root_also_gets_its_rules_at_vhost_scope():
    """Context-scope rules broke !-f for flat docroots too, not just nested ones."""
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        rewrite_mode="front_controller",
    )
    assert "docRoot                   /home/siteuser/example.test/public_html" in rendered
    assert "REQUEST_FILENAME" not in _context_block(rendered)
    assert "RewriteRule ^(.*)$ /index.php [QSA,L]" in _vhost_scope(rendered)


def test_rewrite_mode_none_emits_no_front_controller_rules():
    rendered = openlitespeed.render_vhost(
        "example.test",
        "/home/siteuser/example.test",
        app_type="php",
        php_version="8.4",
        rewrite_mode="none",
        ssl_enabled=False,
    )
    assert "REQUEST_FILENAME" not in rendered


# ---------------------------------------------------------------------------
# PHP error logs must be writable by the site's PHP and private to the site
# ---------------------------------------------------------------------------
def _helper_function(name: str) -> str:
    text = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{")
    return text[start:text.index("\n}\n", start)]


def _helper_text() -> str:
    return (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def test_php_error_logs_are_not_under_the_closed_ols_log_dir():
    """/var/log/openlitespeed is 2770 www-data:opanel-sites so tenants cannot
    read each other's access logs. The site's PHP (its own uid, not in
    opanel-sites) could not reach a php_error.log there either, so every PHP
    error landed in the server-wide stderr.log and the Error tab was empty."""
    path = openlitespeed._php_error_log_path("example.test").as_posix()
    assert path == "/var/log/opanel-php/example.test/php_error.log"
    assert not path.startswith("/var/log/openlitespeed/")


def test_the_php_log_root_is_traverse_only_and_each_site_dir_private():
    body = _helper_function("ensure_php_log_dir")
    assert 'install -d -o root -g root -m 0711 "$PHP_LOG_ROOT"' in body
    assert 'install -d -o "$user" -g "$user" -m 0750 "${PHP_LOG_ROOT}/${domain}"' in body
    assert 'PHP_LOG_ROOT="/var/log/opanel-php"' in _helper_text()


def test_the_log_dir_is_made_even_when_the_vhost_is_unchanged():
    text = _helper_text()
    start = text.index("  ols-vhost-write|ols-vhost-write-defer)")
    block = text[start:text.index(";;", start)]
    assert block.index("ensure_php_log_dir") < block.index('echo "vhost unchanged')


def test_the_error_tab_reads_the_new_location_and_delete_removes_it():
    body = _helper_function("read_site_log")
    assert 'readlink -m "${PHP_LOG_ROOT}/${domain}/php_error.log"' in body
    assert '"${PHP_LOG_ROOT}"/*) ;;' in body
    text = _helper_text()
    start = text.index("  ols-vhost-delete)")
    assert '"${PHP_LOG_ROOT:?}/${safe_domain}"' in text[start:text.index(";;", start)]
