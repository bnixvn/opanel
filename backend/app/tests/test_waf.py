import pytest

from app.services import waf


def test_default_rules_only_cover_wordpress_laravel_and_php():
    definitions = waf.default_rule_definitions()

    assert {rule["category"] for rule in definitions} == {"Laravel", "PHP", "WordPress"}
    assert all(rule["enabled_default"] for rule in definitions)


def test_legacy_heavy_rule_ids_are_mapped_or_ignored():
    assert waf.validate_enabled_rule_ids([
        "general-sensitive-files",
        "general-path-traversal",
        "general-sqli",
        "general-xss",
        "general-command-injection",
    ]) == ["php-sensitive-files", "php-path-traversal", "php-runtime-probes"]


def test_render_site_rules_only_includes_selected_wordpress_rule():
    content = waf.render_site_rules("example.com", ["wordpress-sensitive-files"])

    assert "id:1001101" in content
    assert "id:1001102" not in content
    assert "id:1001201" not in content
    assert "id:1001301" not in content


def test_render_site_rules_includes_laravel_and_php_rules():
    content = waf.render_site_rules("example.com", ["laravel-sensitive-files", "php-sensitive-files"])

    assert "id:1001201" in content
    assert "id:1001301" in content


def test_wordpress_xmlrpc_rule_blocks_xmlrpc():
    content = waf.render_site_rules("example.com", ["wordpress-xmlrpc-author-scan"])

    assert "id:1001102" in content
    assert "/xmlrpc\\.php" in content
    assert "id:1001103" in content


def test_wordpress_wp2shell_rules_block_batch_paths():
    content = waf.render_site_rules("example.com", ["wordpress-wp2shell"])

    assert "id:1000001" in content
    assert "id:1000002" in content
    assert "/wp-json/batch/v1" in content
    assert "/batch/v1" in content


def test_default_rules_do_not_scan_request_body_or_headers():
    content = waf.render_site_rules("example.com", [rule["id"] for rule in waf.DEFAULT_RULES])

    assert "REQUEST_BODY" not in content
    assert "REQUEST_HEADERS" not in content


def test_unknown_rule_ids_are_rejected():
    with pytest.raises(ValueError, match="Unknown WAF rule"):
        waf.validate_enabled_rule_ids(["joomla-sensitive-files"])


def test_parse_access_log_line_marks_xmlrpc_403_as_blocked():
    line = '36.67.165.162 - - [27/Jul/2026:02:03:54 +0000] "POST /xmlrpc.php HTTP/1.1" 403 441 "-" "Jetpack by WordPress.com"'

    entry = waf.parse_access_log_line(line, "example.com")

    assert entry["verdict"] == "block"
    assert entry["method"] == "POST"
    assert entry["path"] == "/xmlrpc.php"
    assert entry["status"] == 403
    assert entry["reason"] == "Block WordPress XML-RPC"


def test_parse_access_log_line_marks_200_as_allowed():
    line = '47.82.49.89 - - [27/Jul/2026:10:39:39 +0000] "GET / HTTP/1.1" 200 1200 "-" "Mozilla/5.0"'

    entry = waf.parse_access_log_line(line, "example.com")

    assert entry["verdict"] == "allow"
    assert entry["reason"] == "Allowed"


def test_parse_access_log_line_marks_wp2shell_paths_as_blocked():
    line = '198.51.100.23 - - [27/Jul/2026:10:40:12 +0000] "POST /wp-json/batch/v1 HTTP/1.1" 403 0 "-" "Mozilla/5.0"'

    entry = waf.parse_access_log_line(line, "example.com")

    assert entry["verdict"] == "block"
    assert entry["reason"] == "Block wp2shell Path"


def test_sync_site_rules_uses_the_deferred_subcommand_when_asked(monkeypatch):
    seen = {}

    def fake_privileged(command, helper_args=None, check=False, fallback=None, **kwargs):
        seen["command"] = command
        return waf.CommandResult(command, 0, "", "")

    monkeypatch.setattr(waf.shell, "privileged", fake_privileged)

    waf.sync_site_rules("example.com", set(), "")
    assert seen["command"] == "waf-site-save"

    waf.sync_site_rules("example.com", set(), "", defer_reload=True)
    assert seen["command"] == "waf-site-save-defer"


def test_access_log_report_filters_and_paginates(monkeypatch):
    payload = "\n".join([
        "opanel_LOG_PATH=example.com\t/var/log/openlitespeed/example.com.access.log",
        'example.com\t47.82.49.89 - - [27/Jul/2026:10:39:39 +0000] "GET / HTTP/1.1" 200 1200 "-" "Mozilla/5.0"',
        'example.com\t36.67.165.162 - - [27/Jul/2026:10:39:40 +0000] "POST /xmlrpc.php HTTP/1.1" 403 441 "-" "Jetpack by WordPress.com"',
    ])

    def fake_privileged(command, helper_args=None, check=False, fallback=None, **kwargs):
        assert command == "waf-access-log-read"
        assert helper_args == ["5000", "example.com"]
        return waf.CommandResult(command, 0, payload, "")

    monkeypatch.setattr(waf.shell, "privileged", fake_privileged)

    report = waf.access_log_report(["example.com"], domain="example.com", verdict="block", limit=10)

    assert report["total"] == 1
    assert report["entries"][0]["path"] == "/xmlrpc.php"
    assert report["entries"][0]["reason"] == "Block WordPress XML-RPC"
