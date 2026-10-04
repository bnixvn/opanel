"""An install or update never stops to ask a question nobody sees.

Installs "hung" twice (2026-10-04) after needrestart's "No VM guests are running
outdated hypervisor (qemu) binaries" and "Failed to enable unit: Refusing to
operate on alias name or linked unit file: lsws.service". Right after that the
installer ran apt with its output sent to /dev/null and then downloaded the
ionCube loaders silently, once per PHP version, with up to five minutes each.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
UPDATE = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")


def _first_apt_call(text: str) -> int:
    return min(m.start() for m in re.finditer(r"(?m)^\s*(?:DEBIAN_FRONTEND=\S+\s+)?apt-get ", text))


def test_no_package_prompt_can_stop_an_install_or_an_update():
    for name, text in (("install.sh", INSTALL), ("update.sh", UPDATE)):
        env = text.index("NEEDRESTART_SUSPEND=1")
        assert env < _first_apt_call(text), f"{name}: needrestart must be suspended before apt runs"
        line = text[text.rindex("\n", 0, env):text.index("\n", env)]
        assert "DEBIAN_FRONTEND=noninteractive" in line and line.lstrip().startswith("export")
        assert '"--force-confdef"; "--force-confold"' in text, f"{name}: dpkg must not ask about config files"
        assert "DPkg::Lock::Timeout" in text, f"{name}: apt must wait for the dpkg lock, not fail"
        assert "export APT_CONFIG=" in text


def test_openlitespeed_is_enabled_by_its_unit_not_its_alias():
    for text in (INSTALL, UPDATE):
        assert not re.search(r"systemctl\s+enable[^\n]*\blsws\b", text)
    assert "systemctl enable --now lshttpd" in INSTALL


def test_ioncube_is_downloaded_once_and_cannot_stop_the_install():
    assert INSTALL.count("fetch_ioncube_loaders)") == 1
    loop = INSTALL[INSTALL.index('if ioncube_dir="$(fetch_ioncube_loaders)"'):]
    loop = loop[:loop.index("\n  fi\n")]
    assert "for version in $PHP_VERSIONS" in loop
    assert "curl" not in loop, "the download happens once, before the per-version loop"
    fetch = INSTALL[INSTALL.index("fetch_ioncube_loaders() {"):]
    fetch = fetch[:fetch.index("\n}\n")]
    assert "--max-time" in fetch and "fail " not in fetch
    install = INSTALL[INSTALL.index("install_ioncube_loader() {"):]
    install = install[:install.index("\n}\n")]
    assert "fail " not in install and "curl" not in install


def test_every_download_in_the_installer_has_a_time_limit():
    for match in re.finditer(r"\bcurl\b[^\n]*", INSTALL):
        line = match.group(0)
        if line.lstrip().startswith("#") or "curl is guaranteed" in line or "(curl" in line:
            continue
        if re.search(r"\bcurl\s+-", line):
            assert "--max-time" in line, line


def test_a_long_hostname_still_gets_a_panel_certificate():
    """A CN holds at most 64 characters. A GitHub runner's fully qualified name
    is longer: openssl refused, the panel had no certificate and served only its
    TLS recovery page (the first install smoke test, 2026-10-04)."""
    helper = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
    body = helper[helper.index("panel_self_signed_ensure() {"):]
    body = body[:body.index("\n}\n")]
    assert '(( ${#subject_cn} <= 64 )) || subject_cn="opanel"' in body
    assert '-subj "/CN=${subject_cn}"' in body and 'subjectAltName=DNS:${cn}' in body
    assert ">/dev/null 2>&1; then" not in body, "the openssl error is reported, not discarded"
