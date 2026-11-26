"""/etc/opanel must let the sites' PHP reach their account's ini.

Each account's disable_functions and OPcache file cache live in
/etc/opanel/php-users/<user>/ (the vhost's PHP_INI_SCAN_DIR), read by the
site's PHP running as the site's own user. The panel certificate's install and
renewal hook set /etc/opanel 0750 root:opanel, and opanelctl's fix-permissions
chmod-ed every entry of it 0640, directories included: on 160.236.192.53 the
four sites ran with no disabled function at all while OPanel showed its list.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
CTL = (ROOT / "installer" / "files" / "opanelctl").read_text(encoding="utf-8")
UPDATE = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")


def test_nothing_shuts_etc_opanel_to_0750():
    assert not re.search(r"-m 0750 /etc/opanel\b(?!/)", HELPER)
    assert "chmod 0750 /etc/opanel " not in CTL and "chmod 0750 /etc/opanel 2>" not in CTL
    assert HELPER.count("install -d -o root -g opanel -m 0751 /etc/opanel") >= 4


def test_fix_permissions_touches_files_only():
    assert "chmod 0640 /etc/opanel/*" not in CTL
    assert "find /etc/opanel -maxdepth 1 -type f" in CTL


def test_the_php_users_writers_leave_etc_opanel_root_opanel():
    assert 'install -d -o root -g root -m 0755 /etc/opanel /etc/opanel/php-users' not in HELPER
    assert HELPER.count('install -d -o root -g root -m 0755 /etc/opanel/php-users "$ini_dir"') == 2


def test_the_update_repairs_live_boxes_and_their_renewal_hooks():
    body = UPDATE.split("ensure_etc_opanel_traversable() {", 1)[1].split("\n}\n", 1)[0]
    assert "chmod 0751 /etc/opanel" in body
    assert "s#-m 0750 /etc/opanel #-m 0751 /etc/opanel #" in body
    assert "\nensure_etc_opanel_traversable || true\n" in UPDATE
