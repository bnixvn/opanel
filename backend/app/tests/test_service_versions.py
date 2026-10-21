"""Updates › Services: each service's version, updated one at a time or all.

The operator asked for it on 2026-10-21, right after finding that OpenLiteSpeed
and lsphp never updated on their own. The page must name the version a service
runs and the one apt offers, and an update must touch only that service's
packages: the helper re-checks the names, this module chooses them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import service_versions
from app.services.shell import CommandResult

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")

LISTING = """== installed
openlitespeed 1.9.3-1+noble
lsphp83 8.3.35-1+noble
lsphp83-common 8.3.35-1+noble
lsphp84 8.4.26-1+noble
lsphp84-igbinary 3.2.16-7+noble
lsphp84-mysql 8.4.26-1+noble
mariadb-server 1:10.11.14-0ubuntu0.24.04.1
libmariadb3:amd64 1:10.11.14-0ubuntu0.24.04.1
redis-server 5:7.0.15-1ubuntu0.24.04.5
== upgradable
openlitespeed 1.9.4-1+noble
lsphp84 8.4.27-1+noble
lsphp84-mysql 8.4.27-1+noble
libmariadb3 1:10.11.15-0ubuntu0.24.04.1
tmux 3.4-1ubuntu0.2
== state
inactive
== log
opanel-service-update: started 2026-10-21T01:00:00Z for redis-server
opanel-service-update: finished (exit 0)
"""


def _fake(monkeypatch, stdout=LISTING):
    calls = []

    def privileged(command, helper_args=None, check=True, fallback=None, **_):
        calls.append((command, list(helper_args or [])))
        if command == "service-packages":
            return CommandResult(command, 0, stdout, "")
        return CommandResult(command, 0, "Service update started for 3 package(s)\n", "")

    monkeypatch.setattr(service_versions.shell, "privileged", privileged)
    return calls


def test_each_service_shows_installed_and_offered_version(monkeypatch):
    _fake(monkeypatch)
    result = service_versions.status()
    services = {item["key"]: item for item in result["services"]}
    assert list(services) == ["openlitespeed", "php84", "php83", "mariadb", "redis"]
    assert services["openlitespeed"]["installed_short"] == "1.9.3"
    assert services["openlitespeed"]["available_short"] == "1.9.4"
    assert services["php84"]["label"] == "PHP 8.4"
    assert [u["name"] for u in services["php84"]["updates"]] == ["lsphp84", "lsphp84-mysql"]
    assert services["php83"]["updates"] == []
    # The main package is current but a library of the service is not.
    assert services["mariadb"]["available"] == ""
    assert [u["name"] for u in services["mariadb"]["updates"]] == ["libmariadb3"]
    assert result["pending"] == 4, "tmux is an OS package, not a service"
    assert result["running"] is False
    assert result["last_result"] == {"ok": True, "exit_code": 0}


def test_refresh_asks_apt_for_new_lists(monkeypatch):
    calls = _fake(monkeypatch)
    service_versions.status(refresh=True)
    assert calls == [("service-packages", ["refresh"])]


def test_one_service_updates_only_its_own_packages(monkeypatch):
    calls = _fake(monkeypatch)
    result = service_versions.run(["php84"])
    assert calls[-1] == ("service-packages-upgrade", ["lsphp84", "lsphp84-mysql"])
    assert result["services"] == ["php84"]


def test_update_all_takes_every_service_package_and_nothing_else(monkeypatch):
    calls = _fake(monkeypatch)
    service_versions.run(None)
    assert calls[-1] == ("service-packages-upgrade",
                         ["libmariadb3", "lsphp84", "lsphp84-mysql", "openlitespeed"])


def test_nothing_to_do_and_unknown_services_are_refused(monkeypatch):
    _fake(monkeypatch)
    with pytest.raises(ValueError, match="Already up to date"):
        service_versions.run(["php83"])
    with pytest.raises(ValueError, match="Unknown service"):
        service_versions.run(["docker"])


def test_a_second_run_waits_for_the_first(monkeypatch):
    _fake(monkeypatch, LISTING.replace("== state\ninactive", "== state\nactive"))
    with pytest.raises(ValueError, match="already running"):
        service_versions.run(None)


def test_short_versions_drop_epoch_and_revision():
    assert service_versions.short_version("1:10.11.14-0ubuntu0.24.04.1") == "10.11.14"
    assert service_versions.short_version("22.23.3-1nodesource1") == "22.23.3"
    assert service_versions.short_version("1.9.3") == "1.9.3"
    assert service_versions.short_version("1:2.3.21+dfsg1-2ubuntu6.5") == "2.3.21"


def test_a_fix_within_the_same_version_shows_the_revision(monkeypatch):
    """Ubuntu's security fixes keep 7.0.15; "7.0.15 -> 7.0.15" says nothing."""
    _fake(monkeypatch, LISTING.replace(
        "== upgradable\n", "== upgradable\nredis-server 5:7.0.15-1ubuntu0.24.04.6\n"))
    redis = next(item for item in service_versions.status()["services"] if item["key"] == "redis")
    assert redis["installed_short"] == "7.0.15-1ubuntu0.24.04.5"
    assert redis["available_short"] == "7.0.15-1ubuntu0.24.04.6"


def test_helper_allow_list_covers_every_service_package():
    """The helper refuses anything outside its list; a service the page shows
    but the helper refuses would fail only when someone presses Update."""
    block = HELPER[HELPER.index("SERVICE_PACKAGE_GLOBS=("):]
    block = block[:block.index(")\n")]
    helper_globs = set(re.findall(r"'([^']+)'|([a-z0-9][a-z0-9.+-]*)", block.split("(", 1)[1]))
    helper_globs = {quoted or bare for quoted, bare in helper_globs}
    page_globs = {pattern for *_, patterns in service_versions.SERVICES for pattern in patterns}
    assert page_globs <= helper_globs
    assert "lsphp[0-9]*" in helper_globs


def test_helper_checks_every_name_before_root_runs_apt():
    body = HELPER[HELPER.index("service_packages_upgrade() {"):]
    body = body[:body.index("\n}\n")]
    assert "service_package_allowed" in body and "install ok installed" in body
    assert "^[a-z0-9][a-z0-9+.-]*$" in body
    assert "--only-upgrade" in body, "never installs a package that is not there"
    assert body.index("deny \"a service update is already running\"") < body.index("systemd-run")
