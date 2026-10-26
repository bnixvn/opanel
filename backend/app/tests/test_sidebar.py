"""Navigation as SNPanel lays it out (operator, 2026-10-17: "Làm lại giao diện
cho giống nó"): a short sidebar per role, hubs whose pages are tabs under
one header, and addons reached from the Dashboard rather than the sidebar."""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"
APP_JSX = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
NAV = (FRONTEND / "lib" / "navigation.jsx").read_text(encoding="utf-8")


def _sidebar(role_marker: str) -> list[str]:
    """The keys of one role's sidebar in sidebarItems()."""
    block = NAV[NAV.index("export function sidebarItems(ctx) {"):]
    if role_marker == "admin":
        block = block[block.index("if (ctx.isAdmin) {"):block.index("if (ctx.isReseller) {")]
    elif role_marker == "reseller":
        block = block[block.index("if (ctx.isReseller) {"):]
        block = block[:block.index("  }\n  return [")]
    else:
        block = block[block.index("  }\n  return ["):]
        block = block[:block.index("];")]
    return re.findall(r"\['([\w-]+)', msg\(", block)


def _hub(key: str) -> str:
    start = NAV.index(f"{{ key: '{key}',")
    return NAV[start:NAV.index("] },", start)]


def test_each_role_has_its_short_sidebar():
    assert _sidebar("admin") == ["dashboard", "websites", "databases", "files", "backups", "users",
                                 "server", "security-center", "config"]
    assert _sidebar("reseller") == ["dashboard", "users", "websites", "databases", "files", "backups", "account"]
    assert _sidebar("customer") == ["dashboard", "websites", "databases", "files", "backups", "account"]


def test_the_hubs_hold_the_rest_as_tabs():
    server = re.findall(r"page: '(\w+)'", _hub("server"))
    assert server == ["processes", "usage", "ramUsage", "diskUsage", "traffic", "php", "mysql", "services", "updates"]
    assert re.findall(r"page: '(\w+)'", _hub("security-center")) == ["firewall", "waf", "autossl", "wafLogs", "malware"]
    assert re.findall(r"page: '(\w+)'", _hub("config")) == ["settings", "addons", "notifications"]
    assert re.findall(r"page: '(\w+)'", _hub("account")) == ["security", "usage"]
    # A tab whose addon is off is not shown.
    assert "{ page: 'malware', label: msg('Malware Scanner'), when: (c) => c.malware }" in NAV


def test_the_file_manager_is_a_page_of_its_own():
    """Operator, 2026-10-26: no File manager / SFTP tab row taking room over
    the files. SFTP accounts and the terminal are the Dashboard's tools, and
    keep the sidebar's Files lit."""
    assert "{ key: 'files'," not in NAV
    assert "sftp: 'files', terminal: 'files'" in NAV
    features = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "features.jsx").read_text(encoding="utf-8")
    assert "terminal: { label: msg('Terminal'), icon: 'terminal', page: 'terminal', when: (c) => c.terminal }" in features
    assert "['files', msg('Files and backups'), ['files', 'sftp', 'terminal', 'git', 'backups']]" in features


def test_the_file_manager_opens_in_a_browser_tab_of_its_own():
    """Operator, 2026-10-26: as cPanel's, with only the files in it. Every way
    to it - the sidebar, the Dashboard, a website's own button - goes through
    navigateToPage, which opens /filemanager in a new tab instead."""
    navigate = APP_JSX[APP_JSX.index("const navigateToPage = useCallback("):APP_JSX.index("  }, []);", APP_JSX.index("const navigateToPage = useCallback("))]
    assert "if (nextPage === 'files') {" in navigate and "window.open(fileManagerUrl(options.websiteId), '_blank', 'noopener');" in navigate
    assert "navigateToPage('files', { websiteId: site.id });" in APP_JSX
    # The tab opens on the website it was asked for.
    assert "useState(() => standaloneEditor?.websiteId || fileManagerSiteFromLocation());" in APP_JSX
    # Nothing of the panel around it: no sidebar, no top bar.
    assert "if (page === 'files') return renderStandaloneFiles();" in APP_JSX
    tab = APP_JSX[APP_JSX.index("function renderStandaloneFiles() {"):APP_JSX.index("if (standaloneEditor) return renderStandaloneEditor();")]
    assert "<FilesPage/>" in tab and "{renderChmodDialog()}" in tab
    assert "sidebar" not in tab and "renderTopStats" not in tab


def test_an_addon_is_not_in_the_sidebar():
    sidebars = NAV[NAV.index("export function sidebarItems(ctx) {"):NAV.index("const PARENTS")]
    for addon in ("mail", "dns", "git", "mcp", "notifications", "malware"):
        assert f"['{addon}'," not in sidebars, addon


def test_the_shell_uses_the_navigation():
    assert "const sideItems = sidebarItems(navCtx);" in APP_JSX
    assert "<HubTabs found={activeHub} page={page} navigateToPage={navigateToPage} routeForPage={routeForPage}/>" in APP_JSX
    # The hub's name, then the page, in the top bar.
    assert "{tr(activeHub.hub.title)}</a>" in APP_JSX and 'className="page-crumb-current">{pageTitle}' in APP_JSX
    # A hub's own address opens its first tab.
    assert "if (isHubKey(nextPage)) nextPage = hubLanding(nextPage, navCtxRef.current) || 'dashboard';" in APP_JSX
    for route in ("server: '/server'", "'security-center': '/security-center'", "account: '/account'", "config: '/settings'"):
        assert route in APP_JSX, route
    assert "renderSettingsHub" not in APP_JSX, "Settings is a hub of tabs now, not a page of tiles"
