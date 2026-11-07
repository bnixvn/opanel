"""php2shell's field and file counts go only on a PHP without the fix.

WordPress's menu screen posts ~10 multipart fields per menu item; a 50-item
menu (552 fields) was refused by the 400-field rule on .122 (2026-11-07). The
counts stop exploits only on an unpatched PHP, so a patched site no longer
carries them; every other php2shell rule stays on every site.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import waf

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPDATE = (PROJECT_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
COUNTS = ("id:1003003", "id:1003004")
EVERYWHERE = ("id:1003001", "id:1003002", "id:1003005", "id:1003006", "id:1003007")


@pytest.fixture
def builds(monkeypatch):
    installed = {"8.1": "8.1.34", "8.3": "8.3.36", "8.4": "8.4.26", "8.5": "8.5.12"}
    monkeypatch.setattr(waf, "_php_builds", {})
    monkeypatch.setattr(waf, "php_build", lambda version: installed.get(str(version or ""), ""))
    return installed


@pytest.mark.parametrize("version,fixed", [
    ("8.5", True), ("8.3", True),
    ("8.4", False),            # 8.4.26: one release short of 8.4.27
    ("8.1", False),            # no fixed 8.1 release exists
    ("7.4", False), (None, False),
])
def test_which_php_has_the_fix(builds, version, fixed):
    assert waf.php2shell_fixed(version) is fixed


def test_a_patched_site_has_no_field_or_file_count(builds):
    content = waf.render_site_rules("menu.test", ["php2shell"], php_version="8.5")
    assert not any(rule_id in content for rule_id in COUNTS)
    assert all(rule_id in content for rule_id in EVERYWHERE)


@pytest.mark.parametrize("version", ["8.1", "8.4", None])
def test_an_unpatched_or_unknown_php_keeps_them(builds, version):
    content = waf.render_site_rules("menu.test", ["php2shell"], php_version=version)
    assert all(rule_id in content for rule_id in COUNTS + EVERYWHERE)


def test_a_website_is_rendered_with_its_own_php(monkeypatch):
    seen = {}
    monkeypatch.setattr(waf, "sync_site_rules", lambda domain, ids, custom, **kw: seen.update(kw))
    site = SimpleNamespace(domain="menu.test", php_version="8.3", waf_default_rules="php2shell",
                           waf_custom_rules="", bot_blocking_enabled=True, bot_extra="")
    monkeypatch.setattr(waf, "website_bot_settings", lambda website: {"bot_blocking_enabled": True, "bot_extra": []})
    waf.sync_website_rules(site)
    assert seen["php_version"] == "8.3"


def test_the_build_is_read_from_the_php_itself_without_its_ini():
    import inspect
    source = inspect.getsource(waf.php_build)
    assert '"-n", "-r", "echo PHP_VERSION;"' in source and "/usr/local/lsws/lsphp" in source


class _Db:
    def __init__(self, versions):
        self.versions = versions

    def query(self, *args):
        return self

    def distinct(self):
        return [(version,) for version in self.versions]


def test_sites_are_re_rendered_once_when_what_they_are_made_of_changes(builds, monkeypatch):
    store = {}
    runs = []
    from app.services import panel_settings
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: dict(store))
    monkeypatch.setattr(panel_settings, "_write_raw", lambda data: store.update(data))
    monkeypatch.setattr(waf, "resync_all_websites", lambda db: runs.append(1) or {"total": 3, "failed": []})
    db = _Db(["8.3", "8.4"])
    assert waf.resync_if_rules_changed(db)["resynced"] is True
    assert waf.resync_if_rules_changed(db) == {"resynced": False}, "nothing changed, nothing done"
    builds["8.4"] = "8.4.27"                                      # PHP was updated
    assert waf.resync_if_rules_changed(db)["resynced"] is True
    assert len(runs) == 2


def test_a_failed_resync_is_tried_again_next_time(builds, monkeypatch):
    store = {}
    from app.services import panel_settings
    monkeypatch.setattr(panel_settings, "_read_raw", lambda: dict(store))
    monkeypatch.setattr(panel_settings, "_write_raw", lambda data: store.update(data))
    monkeypatch.setattr(waf, "resync_all_websites", lambda db: {"total": 1, "failed": ["menu.test"]})
    waf.resync_if_rules_changed(_Db(["8.3"]))
    assert waf.RULES_FINGERPRINT_KEY not in store


def test_every_update_asks_for_it():
    assert "waf.resync_if_rules_changed(db)" in UPDATE
