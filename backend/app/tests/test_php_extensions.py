"""PHP extensions on the PHP config page: status, install, remove."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import php

HELPER = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")

STATUS = """ext apcu available optional
ext curl installed core
ext igbinary installed core
ext imagick installed core
ext imap available optional
ext intl installed core
ext ldap available optional
ext mailparse available optional
ext memcached installed optional
ext msgpack available optional
ext mysql installed core
ext opcache installed core
ext pgsql missing optional
ext pspell available optional
ext redis installed core
ext snmp available optional
ext sqlite3 installed core
ext sybase available optional
ext tidy available optional
module Core
module mysqli
module redis
module Zend OPcache
module the ionCube PHP Loader
"""


@pytest.fixture
def helper(monkeypatch):
    calls = []

    def fake_privileged(command, helper_args=None, **kw):
        calls.append((command, list(helper_args or [])))
        out = STATUS if command == "php-ext-status" else f"done {command}"
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(php.shell, "privileged", fake_privileged)
    monkeypatch.setattr(php, "list_installed_php", lambda: ["8.3", "8.4"])
    return calls


def _helper_function(name: str) -> str:
    start = HELPER.index(f"\n{name}() {{")
    return HELPER[start:HELPER.index("\n}\n", start)]


def test_the_status_is_read_through_the_helper_and_merged_with_loaded_modules(helper):
    data = php.list_php_extensions("8.3")
    assert helper == [("php-ext-status", ["8.3"])]
    by_name = {e["name"]: e for e in data["extensions"]}
    assert list(by_name) == list(php.PHP_EXTENSIONS)
    assert by_name["mysql"] == {"name": "mysql", "package": "lsphp83-mysql", "description": php.PHP_EXTENSIONS["mysql"],
                                "state": "installed", "core": True, "loaded": True}
    assert by_name["opcache"]["loaded"] is True            # "Zend OPcache", not "opcache"
    assert by_name["memcached"]["state"] == "installed" and by_name["memcached"]["loaded"] is False
    assert by_name["pgsql"]["state"] == "missing"
    assert by_name["apcu"]["core"] is False
    assert "the ionCube PHP Loader" in data["modules"]


def test_install_and_remove_go_through_the_helper(helper):
    php.install_php_extension("8.4", "apcu")
    php.remove_php_extension("8.4", "apcu")
    assert helper == [("php-ext-install", ["8.4", "apcu"]), ("php-ext-remove", ["8.4", "apcu"])]


@pytest.mark.parametrize("version, name", [("8.3", "ioncube"), ("8.3", "apcu; rm -rf /"), ("8.3", "dev"), ("7.0", "apcu"), ("8.5", "apcu")])
def test_unknown_extensions_and_versions_are_refused_before_the_helper(helper, version, name):
    with pytest.raises(ValueError):
        php.install_php_extension(version, name)
    assert helper == []


def test_a_helper_refusal_reaches_the_admin(monkeypatch):
    monkeypatch.setattr(php, "list_installed_php", lambda: ["8.3"])
    monkeypatch.setattr(php.shell, "privileged", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="opanel-helper: removing lsphp83-igbinary would also remove lsphp83-redis\n"))
    with pytest.raises(RuntimeError, match="^removing lsphp83-igbinary would also remove lsphp83-redis$"):
        php.remove_php_extension("8.3", "igbinary")


def test_the_helper_offers_the_same_extensions_and_guards_them():
    allowed = re.search(r"^PHP_EXT_ALLOWED=\(([^)]*)\)", HELPER, re.M).group(1).split()
    core = re.search(r"^PHP_EXT_CORE=\(([^)]*)\)", HELPER, re.M).group(1).split()
    assert sorted(allowed) == sorted(php.PHP_EXTENSIONS)
    # The panel's own set can never be removed, and ionCube stays with its own installer.
    assert set(core) >= {"curl", "igbinary", "imagick", "intl", "mysql", "opcache", "redis", "sqlite3"}
    assert "ioncube" not in allowed
    remove = _helper_function("php_ext_remove")
    assert 'php_ext_is_core "$ext" && deny' in remove
    assert "apt-get -s remove" in remove           # refuses when apt would take PHP or a core package along
    assert remove.index("apt-get -s remove") < remove.index("apt-get -o DPkg::Lock::Timeout=120 remove -y")
    for fn in ("php_ext_install", "php_ext_remove"):
        body = _helper_function(fn)
        assert 'require_php_ext "$ext"' in body and "restart_openlitespeed" in body


def test_the_php_page_offers_extensions():
    app = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "{renderPhpExtensions()}" in app
    assert "/maintenance/php/extensions/${encodeURIComponent(version)}/${encodeURIComponent(ext.name)}/${action}" in app
    # Core extensions get no remove button.
    assert "(ext.core ? <span /> :" in app
