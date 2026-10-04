"""The server-wide custom WAF rules reach every site that runs the WAF.

opanel-custom.conf (Settings > WAF, and the MCP add_waf_rule tool without a
domain) was included only by opanel-main.conf, which nothing includes: a
server-wide rule never applied anywhere. On .122 an assistant blocked a botnet's
"Chrome/81.0.4044" user agent server-wide and the requests kept getting 200
(2026-10-04). Each site's rules file now includes the server-wide file, so a
rule id used in both would stop ModSecurity loading that site's rules: saving
refuses it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import waf as waf_api
from app.models.entities import User, Website
from app.services import waf
from app.services.shell import CommandResult

HELPER = Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh"
ADMIN = User(id=1, username="root_admin", email="a@example.test", role="admin", is_active=True, hashed_password="x")


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class _DB:
    def __init__(self, sites):
        self.sites = sites

    def query(self, model):
        return _Query(self.sites)

    def add(self, obj):
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def _site(custom=""):
    return Website(id=7, domain="shop.test", owner_id=1, root_path="/home/x/shop.test", linux_user="x",
                   php_version="8.4", app_type="wordpress", status="active", waf_enabled=True,
                   waf_custom_rules=custom)


def test_a_site_rules_file_includes_the_server_wide_rules():
    text = waf.render_site_rules("shop.test", [], "")
    base = text.index("Include /usr/local/lsws/conf/opanel/waf/opanel-base.conf")
    server_wide = text.index(f"Include {waf.SERVER_CUSTOM_RULES_FILE}")
    assert base < server_wide, "the engine settings come first"
    assert waf.SERVER_CUSTOM_RULES_FILE.endswith("/opanel-custom.conf")


def test_the_helper_makes_sure_the_included_file_exists():
    text = HELPER.read_text(encoding="utf-8")
    body = text[text.index("save_waf_site_rules() {"):]
    body = body[:body.index("\n}\n")]
    assert "touch /usr/local/lsws/conf/opanel/waf/opanel-custom.conf" in body


def test_a_rule_id_cannot_be_both_server_wide_and_a_sites_own():
    with pytest.raises(ValueError, match="1090006"):
        waf.check_no_shared_rule_ids('SecRule ARGS "@rx a" "id:1090006,phase:1,deny"',
                                     ['SecRule ARGS "@rx b" "id:1090006,phase:1,deny"'], "the server-wide rules")
    waf.check_no_shared_rule_ids('SecRule ARGS "@rx a" "id:1090007,phase:1,deny"',
                                 ['SecRule ARGS "@rx b" "id:1090006,phase:1,deny"'], "the server-wide rules")


def test_saving_server_wide_rules_refuses_an_id_a_site_uses(monkeypatch):
    saved = []
    monkeypatch.setattr(waf_api.waf, "save_custom_rules", lambda content: saved.append(content) or CommandResult("w", 0, "", ""))
    db = _DB([_site('SecRule ARGS "@rx b" "id:4242,phase:1,deny"')])

    with pytest.raises(HTTPException) as exc:
        waf_api.save_waf_custom_rules(waf_api.WafCustomRulesUpdate(content='SecRule ARGS "@rx a" "id:4242,phase:1,deny"'),
                                      db=db, current_user=ADMIN)
    assert exc.value.status_code == 400 and "4242" in exc.value.detail and saved == []

    waf_api.save_waf_custom_rules(waf_api.WafCustomRulesUpdate(content='SecRule ARGS "@rx a" "id:4243,phase:1,deny"'),
                                  db=db, current_user=ADMIN)
    assert len(saved) == 1


def test_saving_a_sites_rules_refuses_an_id_the_server_wide_rules_use(monkeypatch):
    monkeypatch.setattr(waf_api.waf, "custom_rules",
                        lambda: CommandResult("w", 0, 'SecRule ARGS "@rx b" "id:4242,phase:1,deny"', ""))
    monkeypatch.setattr(waf_api.waf, "save_website_config", lambda *a, **k: pytest.fail("must not save"))
    payload = waf_api.WebsiteWafRulesUpdate(enabled_rule_ids=[], custom_rules='SecRule ARGS "@rx a" "id:4242,phase:1,deny"')

    with pytest.raises(HTTPException) as exc:
        waf_api.save_website_waf(payload, 7, db=_DB([_site()]), current_user=ADMIN)
    assert exc.value.status_code == 400 and "4242" in exc.value.detail


def test_the_mcp_tool_passes_the_database_to_the_server_wide_save():
    source = (Path(waf.__file__).parent / "mcp.py").read_text(encoding="utf-8")
    calls = [line for line in source.splitlines() if "waf_api.save_waf_custom_rules(" in line]
    assert calls and all("db=ctx.db" in line for line in calls)


def test_a_rule_without_an_id_or_with_an_open_quote_is_refused():
    """What took every site's WAF down on the test box: OLS logged "Rules must
    have an ID" and served the sites without their rules."""
    with pytest.raises(ValueError, match="no id"):
        waf.check_rule_basics('SecRule REQUEST_HEADERS:User-Agent "@contains x" "phase:1,deny"')
    with pytest.raises(ValueError, match="unclosed quote"):
        waf.check_rule_basics('SecRule REQUEST_HEADERS:User-Agent "@contains x "id:1,phase:1,deny"')
    # The rule tried on the test box: quotes paired, an id in the text, but the
    # actions are not one quoted argument, so ModSecurity sees no id.
    with pytest.raises(ValueError, match="should read"):
        waf.check_rule_basics('SecRule REQUEST_HEADERS:User-Agent "@contains brokenrule "id:1095001,phase:1,deny')
    # Comments, continuation lines, escaped quotes and chains are fine.
    waf.check_rule_basics(
        '# a comment with "one quote\n'
        'SecRule ARGS "@rx \\"a\\"" \\\n'
        '    "id:1095002,phase:1,deny"\n'
        'SecAction "id:1095003,phase:1,pass,nolog"\n'
        'SecRule REQUEST_METHOD "@streq POST" "id:1095004,phase:1,chain,deny"\n'
        '    SecRule ARGS:action "@streq share"\n'
        'SecRule REQUEST_URI "@beginsWith /a" "id:1095005,phase:1,deny,status:403,msg:\'x, chained\'"'
    )
    # After a chain ends, the next rule needs its id again.
    with pytest.raises(ValueError, match="no id"):
        waf.check_rule_basics('SecRule A "@rx a" "id:1,phase:1,chain,deny"\nSecRule B "@rx b"\nSecRule C "@rx c" "phase:1,deny"')
    with pytest.raises(HTTPException) as exc:
        waf_api.save_waf_custom_rules(waf_api.WafCustomRulesUpdate(content='SecRule ARGS "@rx a" "phase:1,deny"'),
                                      db=_DB([]), current_user=ADMIN)
    assert exc.value.status_code == 400
