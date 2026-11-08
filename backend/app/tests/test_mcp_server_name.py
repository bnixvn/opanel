"""Each panel and account gets its own MCP server name (operator, 2026-11-08):
every server was "opanel", so a client connected to several VPSes could not
tell them apart, nor add a second one under the same name."""
from pathlib import Path

APP = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
MCP = (Path(__file__).resolve().parents[1] / "services" / "mcp.py").read_text(encoding="utf-8")


def _snippets() -> str:
    start = APP.index("  function mcpClientSnippets(token) {")
    return APP[start:APP.index("\n  }\n", start)]


def test_the_setup_snippets_use_the_name_not_a_fixed_one():
    body = _snippets()
    assert "const name = mcpName;" in body
    assert "--transport http ${name} " in body
    assert "mcpServers: { [name]:" in body and "servers: { [name]:" in body
    assert "http opanel " not in body and "{ opanel:" not in body


def test_the_name_comes_from_the_panel_address_and_the_account():
    assert "function defaultMcpServerName(hostname, username)" in APP
    assert "defaultMcpServerName(window.location.hostname, currentUser?.username)" in APP
    assert "replace(/^(panel|www)\\./, '')" in APP and ".slice(0, 40)" in APP


def test_the_server_says_which_panel_it_is():
    assert '"title": f"OPanel · {settings.panel_domain}" if settings.panel_domain else "OPanel"' in MCP
