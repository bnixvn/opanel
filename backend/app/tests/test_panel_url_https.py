"""The panel's address is https from the first install.

app/server.py never serves the panel over plain HTTP; without Let's Encrypt it
uses a self-signed default. The installer still recorded http:// for an IP or a
domain without Let's Encrypt, so login.txt and the install summary showed an
address that did not connect (operator, 2026-10-05), and phpMyAdmin's sign-on
called the API over http:// on every server without a panel certificate and
got no answer.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
UPDATE = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _function(text: str, name: str) -> str:
    body = text[text.index(f"{name}() {{"):]
    return body[:body.index("\n}\n")]


def test_the_installer_never_records_an_http_panel_address():
    ask = _function(INSTALL, "ask_panel_url")
    assert not re.search(r'PANEL_URL="http://', ask)
    assert 'PANEL_URL="https://${SERVER_IP}:${PANEL_PORT}"' in ask


def test_phpmyadmin_sign_on_calls_the_api_over_https_everywhere():
    for text, name in ((INSTALL, "write_tools_vhost_config"), (HELPER, "refresh_tools_ols"),
                       (UPDATE, "write_tools_nginx_config")):
        body = _function(text, name)
        assert re.search(r'api_scheme="https"', body), name
        assert 'api_scheme="http"' not in body, name


def test_an_update_puts_https_into_an_old_login_txt():
    assert "sed -i 's#^Panel URL: http://#Panel URL: https://#' /root/login.txt" in UPDATE
