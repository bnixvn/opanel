"""The website list and one website's page, laid out as SNPanel's (2026-10-17).

The list's inline panels (settings, logs, terminal, WordPress) became a page per
website with tabs, at /website/<domain>/<tab>; creating a website moved into a
side sheet. What OPanel had and SNPanel has not must still be there.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
APP = (SRC / "App.jsx").read_text(encoding="utf-8")
NAV = (SRC / "lib" / "navigation.jsx").read_text(encoding="utf-8")
ROUTES = (SRC / "lib" / "websites.js").read_text(encoding="utf-8")
LIST = (SRC / "pages" / "Websites.jsx").read_text(encoding="utf-8")
DETAIL = (SRC / "pages" / "WebsiteDetail.jsx").read_text(encoding="utf-8")


def test_a_website_has_an_address_of_its_own():
    assert "if (isWebsitePath(normalized)) return 'website-detail';" in APP
    assert "if (page === 'website-detail') return <WebsiteDetailPage key={routeKey}/>;" in APP
    # The overview is the website's bare address; every other tab adds itself.
    assert "tab && tab !== 'overview' ? `/${tab}` : ''" in ROUTES
    # The page lights Websites in the sidebar.
    assert "'website-detail': 'websites'" in NAV


def test_the_page_has_its_tabs():
    for tab in ("overview", "domains", "ssl", "php", "security", "logs", "advanced"):
        assert f"{{ id: '{tab}'," in DETAIL, tab
    # SSL and WAF are the same forms as their own pages, for this website.
    assert "renderSslManage(true)" in DETAIL and "renderWafSite(true)" in DETAIL
    assert "{!embedded && <WebsiteSelect />}" in APP


def test_creating_keeps_opanels_own_choices():
    # Whose the site is, and every way its certificate can come.
    assert "setSiteOwnerId(e.target.value)" in LIST
    for mode in ("'letsencrypt'", "'wildcard'", "'existing'", "'manual'"):
        assert mode in LIST, mode
    # A wildcard over this server's DNS when DNS Manager is on.
    assert "renderWildcardProvider(wildcardVia" in LIST


def test_an_administrator_gets_a_customers_shell_only_by_logging_in_as_them():
    section = DETAIL[DETAIL.index("function TerminalSection("):DETAIL.index("function AdvancedTab(")]
    assert "{canOpenTerminal && open && <div className=\"wsd-terminal\">" in section
    assert "quickLoginUser(owner)" in section


def test_the_old_inline_panels_are_gone():
    for name in ("function renderWebsites(", "function renderNginxEditor(", "function renderWebsiteTerminal(",
                 "function renderWebsiteLogViewer(", "terminalViewer"):
        assert name not in APP, name
