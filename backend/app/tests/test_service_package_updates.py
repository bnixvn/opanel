"""The web server and PHP get their security fixes without anyone pressing a button.

unattended-upgrades followed only Ubuntu's archive, so OpenLiteSpeed and lsphp
from LiteSpeed's repository changed only through "OS update" (operator's check,
2026-10-21). The same check found the panel's apt config asking unattended
upgrades to remove "unused" packages: on .41 it removed Ubuntu's php8.3
extensions overnight on 2026-10-19.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
UPDATE = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
PATTERN = '"o=LiteSpeedTech,a=${distro_codename}";'


def _body(text: str, name: str) -> str:
    body = text[text.index(f"{name}() {{"):]
    return body[:body.index("\n}\n")]


def test_unattended_upgrades_never_removes_unused_packages():
    assert 'Remove-Unused-Dependencies "true"' not in HELPER
    assert 'Remove-Unused-Dependencies "false"' in HELPER
    body = _body(UPDATE, "migrate_protect_php_from_autoremove")
    assert 'Remove-Unused-Dependencies "false"' in body and "apt-mark manual" in body


def test_litespeed_repository_is_an_unattended_origin():
    writer = _body(HELPER, "write_service_update_origins")
    assert PATTERN in writer
    assert "<<'APT'" in writer, "quoted heredoc: apt expands ${distro_codename}, not bash"
    assert "write_service_update_origins" in _body(HELPER, "configure_unattended_upgrades")


def test_existing_servers_get_the_origin_on_update():
    migration = _body(UPDATE, "migrate_service_update_origins")
    assert PATTERN in migration and "<<'APT'" in migration
    assert "52opanel-service-updates" in migration and "52opanel-service-updates" in HELPER
    calls = [line.strip() for line in UPDATE.splitlines()]
    assert calls.count("migrate_service_update_origins") == 1
    assert calls.count("migrate_protect_php_from_autoremove") == 1
