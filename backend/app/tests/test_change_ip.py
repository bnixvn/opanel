"""`opanel change-ip` has a script to run, and it moves the hostname on Settings
too (operator, 2026-11-09: the panel hostname in Settings kept the old IP).

Nothing installed /usr/local/sbin/opanel-change-ip before - the installer
looked for a change_IP.sh this repo never had - and Settings reads panel_url
from panel-settings.json before the .env."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (ROOT / "installer" / "files" / "opanel-change-ip.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
UPDATE = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
CTL = (ROOT / "installer" / "files" / "opanelctl").read_text(encoding="utf-8")


def test_install_and_update_put_the_script_where_opanel_change_ip_looks():
    target = "/usr/local/sbin/opanel-change-ip"
    assert f'"${{SCRIPT_DIR}}/files/opanel-change-ip.sh" {target}' in INSTALL
    assert f'"$SOURCE_DIR/installer/files/opanel-change-ip.sh" {target}' in UPDATE
    assert f'local script="{target}"' in CTL
    assert "change_IP.sh" not in INSTALL


def test_it_rewrites_what_settings_reads_and_the_dns_zones():
    assert 'DATA_DIR="${opanel_DATA_DIR:-/var/lib/opanel}"' in SCRIPT and 'collect_tree "$DATA_DIR"' in SCRIPT
    assert 'settings_file="$DATA_DIR/panel-settings.json"' in SCRIPT
    assert '"$APP_DIR/backend/.env"' in SCRIPT and "/usr/local/lsws/conf/httpd_config.conf" in SCRIPT
    assert "UPDATE records SET content = ? WHERE id = ?" in SCRIPT and "pdnsutil increase-serial" in SCRIPT
    # Whole addresses only: 1.2.3.4 is not touched inside 11.2.3.45.
    assert "(?<![0-9.])\\\\Q$old_ip\\\\E(?![0-9.])" in SCRIPT


def test_every_change_is_backed_up_first():
    loop = SCRIPT[SCRIPT.index('for file in "${files[@]}"; do'):]
    loop = loop[:loop.index("\ndone\n")]
    assert loop.index('cp -a -- "$file" "$dest"') < loop.index("perl -0pi")
