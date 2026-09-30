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
    assert "127.0.0.1:8088" in body and "127.0.0.1:7080" in body and "admin_config.conf" in body
    hygiene = HELPER.split("  log-hygiene)", 1)[1].split(";;", 1)[0]
    assert "ensure_ols_defaults_private" in hygiene


def test_the_demo_site_edit_works_on_openlitespeeds_own_spelling(tmp_path):
    """OLS writes "virtualHost Example{" (capital H, no space); a first version
    matched "virtualhost Example {" and left the vhost behind on a real box."""
    import subprocess
    import sys

    script = _function(HELPER, "ensure_ols_defaults_private").split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    httpd = tmp_path / "httpd_config.conf"
    httpd.write_text(
        "extProcessor lsphp{\n    type lsapi\n}\n\n"
        "virtualHost Example{\n    vhRoot                   Example/\n"
        "    configFile               conf/vhosts/Example/vhconf.conf\n}\n\n"
        "listener Default{\n    address                  *:8088\n    secure                   0\n"
        "    map                      Example *\n}\n\n"
        "vhTemplate centralConfigLog{\n    listeners                Default\n}\n", encoding="utf-8")
    admin = tmp_path / "admin_config.conf"
    admin.write_text("listener adminListener{\n  address                 *:7080\n  secure                  1\n}\n", encoding="utf-8")
    out = subprocess.run([sys.executable, "-", str(httpd), str(admin)], input=script, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "demo site, WebAdmin"
    conf = httpd.read_text(encoding="utf-8")
    assert "Example" not in conf
    assert "address                  127.0.0.1:8088" in conf and "listeners                Default" in conf
    assert "extProcessor lsphp{" in conf
    assert "127.0.0.1:7080" in admin.read_text(encoding="utf-8")
    # A second run changes nothing.
    again = subprocess.run([sys.executable, "-", str(httpd), str(admin)], input=script, capture_output=True, text=True)
    assert again.stdout.strip() == ""


def test_phpmyadmins_php_starts_on_demand_and_idles_out():
    for source in (HELPER, INSTALL):
        tools = source.split("extprocessor lsphp${default_ver_no_dot} {", 1)[1].split("\n}", 1)[0]
        assert "runOnStartUp            0" in tools and "runOnStartUp            1" not in tools
        # OLS's name for it; "maxIdleTime" was ignored and the process stayed
        # (seen on the test VPS, 2026-09-30).
        assert "extMaxIdleTime          300" in tools and "\n  maxIdleTime" not in tools


def test_host_trim_masks_only_what_a_vps_does_not_use_and_records_it():
    body = _function(HELPER, "host_trim")
    units = HELPER.split("HOST_TRIM_UNITS=(", 1)[1].split(")", 1)[0].split()
    assert units[:6] == ["ModemManager.service", "udisks2.service", "upower.service", "fwupd.service",
                         "fwupd-refresh.service", "fwupd-refresh.timer"]
    # Performance Co-Pilot from provider images: services and timers (2026-09-30).
    pcp = [unit for unit in units if unit.startswith("pm")]
    assert {"pmcd.service", "pmproxy.service", "pmlogger.service", "pmie.service"} <= set(pcp)
    assert len(pcp) == len(units) - 6
    # Never the things a server needs.
    for kept in ("unattended-upgrades", "ssh", "systemd-", "cron", "snapd", "qemu-guest-agent"):
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
    # Through sudo as opanel, like every other helper call: run as root directly
    # the helper refuses (SUDO_USER check) and "|| true" would hide it.
    assert 'sudo -u opanel env HOME="$APP_DIR" sudo -n /usr/local/sbin/opanel-helper host-trim || true' in INSTALL
    assert '  host-trim)\n    [[ $# -eq 0 ]] || deny "usage: host-trim"\n    host_trim' in HELPER
    # An update does not touch the host's services: that stays an install-time choice.
    update = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    assert "host-trim" not in update
