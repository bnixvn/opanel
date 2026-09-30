"""What a fresh install leaves running that a hosting VPS never uses.

Measured on a fresh Ubuntu 24.04 VPS (2 GB, 2026-09-30): the OpenLiteSpeed
demo site and WebAdmin answered from the internet, phpMyAdmin's PHP sat in
memory from boot, and the cloud image's hardware services (multipathd, fwupd,
ModemManager, udisks2, upower) held ~70 MB between them.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    body = source.split(f"\n{name}() {{", 1)[1]
    return body.split("\n}\n", 1)[0]


def test_the_ols_demo_site_and_webadmin_are_closed_on_every_update():
    body = _function(HELPER, "ensure_ols_defaults_private")
    assert r"^virtualhost Example\s*\{" in body
    assert "127.0.0.1:8088" in body and "map[ \\t]+Example" in body
    assert "127.0.0.1:7080" in body and "admin_config.conf" in body
    hygiene = HELPER.split("  log-hygiene)", 1)[1].split(";;", 1)[0]
    assert "ensure_ols_defaults_private" in hygiene


def test_phpmyadmins_php_starts_on_demand_and_idles_out():
    for source in (HELPER, INSTALL):
        tools = source.split("extprocessor lsphp${default_ver_no_dot} {", 1)[1].split("\n}", 1)[0]
        assert "runOnStartUp            0" in tools and "runOnStartUp            1" not in tools
        assert "maxIdleTime             300" in tools


def test_host_trim_masks_only_what_a_vps_does_not_use_and_records_it():
    body = _function(HELPER, "host_trim")
    units = HELPER.split("HOST_TRIM_UNITS=(", 1)[1].split(")", 1)[0].split()
    assert units == ["ModemManager.service", "udisks2.service", "upower.service", "fwupd.service",
                     "fwupd-refresh.service", "fwupd-refresh.timer"]
    # Never the things a server needs.
    for kept in ("unattended-upgrades", "ssh", "systemd-", "cron", "snapd", "pmcd"):
        assert kept not in " ".join(units)
    assert 'systemctl mask "$1"' in body and 'echo "$1" >>"$record"' in body
    # multipath only when no multipath device exists.
    assert "! multipath -l 2>/dev/null | grep -q ." in body


def test_apache_is_purged_only_when_phpmyadmin_stays():
    body = _function(HELPER, "host_trim")
    assert "! systemctl is-active --quiet apache2" in body
    assert 'apt-get -s purge "${purge[@]}"' in body and '|| simulated="phpmyadmin"' in body
    assert 'if ! grep -qx "phpmyadmin" <<<"$simulated"; then' in body
    # Its snakeoil certificate serves the tools port.
    assert "apt-mark manual ssl-cert" in body
    assert "ssl-cert-snakeoil" in HELPER


def test_the_installer_trims_the_host_unless_told_not_to():
    assert '"${KEEP_SYSTEM_SERVICES:-no}" != "yes"' in INSTALL
    assert "/usr/local/sbin/opanel-helper host-trim || true" in INSTALL
    assert '  host-trim)\n    [[ $# -eq 0 ]] || deny "usage: host-trim"\n    host_trim' in HELPER
    # An update does not touch the host's services: that stays an install-time choice.
    update = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    assert "host-trim" not in update
