"""The sidebar holds day-to-day hosting work; everything else is on Settings."""
import re
from pathlib import Path

APP_JSX = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    begin = APP_JSX.index(start)
    return APP_JSX[begin:APP_JSX.index(end, begin)]


def test_the_sidebar_is_only_the_everyday_pages():
    nav = _block("  const navSections = [", "  const navItems =")
    keys = re.findall(r"\['(\w+)', tr\(", nav)
    assert keys == ["dashboard", "websites", "ssl", "databases", "cron", "files", "sftp", "backups", "users", "config"]
    assert "...(isAdmin ? [['users', tr(\"Panel users\"), Users]] : [])" in nav


def test_everything_else_is_on_the_settings_page():
    hub = _block("  const settingsGroups = [", "  const settingsItems =")
    keys = re.findall(r"\['(\w+)', tr\(", hub)
    assert keys == ["firewall", "waf", "malware", "wafLogs", "security", "services", "php", "settings", "updates", "addons"]
    for admin_only in ("firewall", "malware", "services", "php", "settings", "updates", "addons"):
        index = hub.index(f"['{admin_only}', tr(")
        assert "isAdmin ?" in hub[max(0, index - 20):index], admin_only
    assert "  config: '/settings'," in APP_JSX and "  settings: '/panel-settings'," in APP_JSX
    assert "if (page === 'config') return renderSettingsHub();" in APP_JSX


def test_addons_appear_only_while_turned_on():
    addons = _block("  const addonNavItems = [", "  ];")
    assert "...(mcpInfo?.enabled ? [['mcp'" in addons
    assert "...(isAdmin && notifyInfo?.enabled ? [['notifications'" in addons


def test_a_page_opened_from_settings_keeps_settings_lit_and_shows_the_way_back():
    assert "const navKey = settingsPage ? 'config' : page;" in APP_JSX
    assert "className={navKey === key ? 'active' : ''}" in APP_JSX
    assert "onClick={() => navigateToPage('config')}>{tr(\"Settings\")}</button>" in APP_JSX
