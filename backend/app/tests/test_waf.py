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
    assert "id:1001201" not in content
    assert "id:1001301" not in content


def test_render_site_rules_includes_laravel_and_php_rules():
    content = waf.render_site_rules("example.com", ["laravel-sensitive-files", "php-sensitive-files"])

    assert "id:1001201" in content
    assert "id:1001301" in content


def test_default_rules_do_not_scan_request_body_or_headers():
    content = waf.render_site_rules("example.com", [rule["id"] for rule in waf.DEFAULT_RULES])

    assert "REQUEST_BODY" not in content
    assert "REQUEST_HEADERS" not in content


def test_unknown_rule_ids_are_rejected():
    with pytest.raises(ValueError, match="Unknown WAF rule"):
        waf.validate_enabled_rule_ids(["joomla-sensitive-files"])
