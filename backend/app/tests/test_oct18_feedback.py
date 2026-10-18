"""The operator's list of 2026-10-18, as the sources must keep it.

Several of these were calls to functions that no longer existed (a removed
state setter, a renamed one): the click or the change threw and nothing
happened. Those are pinned by name here.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
APP = (SRC / "App.jsx").read_text(encoding="utf-8")


def test_what_needs_attention_is_the_headers_bell():
    assert "<NotificationBell/>" in APP and "import NotificationBell" in APP
    dash = (SRC / "pages" / "Dashboard.jsx").read_text(encoding="utf-8")
    assert "attentionCard" not in dash and "/dashboard/updates" not in dash
    bell = (SRC / "components" / "NotificationBell.jsx").read_text(encoding="utf-8")
    assert "attentionItems(" in bell and "'/dashboard/summary'" in bell


def test_the_dashboard_sections_carry_no_count_and_keep_their_order():
    dash = (SRC / "pages" / "Dashboard.jsx").read_text(encoding="utf-8")
    assert "dash-section-count" not in dash and "--cp-order" not in dash
    ui = (SRC / "shared" / "ui.css").read_text(encoding="utf-8")
    assert "order:var(--cp-order" not in ui


def test_the_file_manager_shortcut_and_sign_out_call_nothing_removed():
    assert "setTerminalViewer" not in APP


def test_the_access_log_filters_have_their_setter():
    uses = re.findall(r"setWafAccessFilter\(", APP)
    assert len(uses) >= 4
    assert "function setWafAccessFilter(key, value) {" in APP


def test_deleting_a_user_refreshes_the_websites_it_took():
    body = APP[APP.index("async function deletePanelUser(user) {"):]
    body = body[:body.index("\n  }\n")]
    assert "loadWebsites()" not in body and "await refreshAll();" in body


def test_the_terminal_box_is_as_tall_as_its_terminal():
    css = (SRC / "pages" / "Websites.css").read_text(encoding="utf-8")
    rule = re.search(r"\.wsd-terminal\{([^}]*)\}", css).group(1)
    assert "height" not in rule


def test_the_mail_domain_field_is_a_picker_not_a_datalist():
    body = APP[APP.index("function renderMailDomains() {"):]
    body = body[:body.index("function ", 30)]
    assert "<datalist" not in body and "<DomainPicker" in body


def test_wordpress_upload_has_its_own_button():
    wp = (SRC / "pages" / "WordPress.jsx").read_text(encoding="utf-8")
    assert "t('Upload plugin')" in wp and "t('Upload theme')" in wp and "wpm-dropzone" in wp


def test_a_removed_php_extension_leaves_no_ini_behind():
    helper = (SRC.parents[1] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
    body = helper[helper.index("php_ext_remove() {"):]
    body = body[:body.index("\n}\n")]
    assert 'purge -y "$pkg"' in body and 'remove -y "$pkg"' not in body
    packages = (SRC.parents[1] / "installer" / "files" / "opanel-packages.sh").read_text(encoding="utf-8")
    assert "purge_lsphp_leftover_configs" in packages and '$1 == "rc"' in packages
