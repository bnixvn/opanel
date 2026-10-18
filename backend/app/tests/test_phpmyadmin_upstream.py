"""phpMyAdmin from upstream, not apt (operator, 2026-10-18: "xài bản tải trực
tiếp trước giờ vẫn login sso được mà" - the directly-downloaded build has
always worked with single sign-on). apt's `phpmyadmin` pulled apache2 and a
dozen php-symfony-*/php-twig/php-phpmyadmin-* packages, all needing Ubuntu Pro
for security fixes; OpenLiteSpeed has always served it, never Apache.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
UPDATE = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
PACKAGES = (ROOT / "installer" / "files" / "opanel-packages.sh").read_text(encoding="utf-8")
PMA = (ROOT / "installer" / "files" / "opanel-phpmyadmin.sh").read_text(encoding="utf-8")


def _function(source: str, name: str, next_name: str = "") -> str:
    body = source.split(f"\n{name}() {{", 1)[1]
    if next_name:
        # Some functions write a heredoc with its own bare "}" lines (the OLS
        # vhost config), which a plain "\n}\n" split cuts off inside: end at
        # the next function's definition instead.
        return body.split(f"\n{next_name}() {{", 1)[0]
    return body.split("\n}\n", 1)[0]


def test_phpmyadmin_is_not_installed_from_apt():
    base = _function(INSTALL, "install_base_packages")
    lines = [line for line in base.splitlines() if "apt-get install -y" in line]
    assert lines and " phpmyadmin " not in f" {lines[0]} "
    assert f'bash "${{SCRIPT_DIR}}/files/opanel-phpmyadmin.sh"' in base


def test_the_download_is_checked_against_the_published_sha256():
    assert "sha256sum" in PMA and ".sha256" in PMA
    assert 'expected" != "$actual"' in PMA
    assert "exit 1" in PMA.split('"$expected" != "$actual"', 1)[1][:200]


def test_installing_again_at_the_same_version_downloads_nothing():
    assert 'cat "$PMA_MARKER"' in PMA and "exit 0" in PMA
    first_check = PMA.index('"$(cat "$PMA_MARKER"')
    assert PMA.index("curl") > first_check


def test_the_setup_wizard_is_removed_and_the_tree_is_world_readable_only():
    assert 'rm -rf "${extracted}/setup"' in PMA
    assert 'find "$extracted" -type f -exec chmod 0644' in PMA
    assert 'chown -R root:root "$extracted"' in PMA


def test_phpmyadmin_reads_its_own_config_file_from_etc():
    """Vanilla phpMyAdmin's default configFile is ROOT_PATH . 'config.inc.php'
    (its own docroot) -- not /etc/phpmyadmin, which is a Debian-only patch."""
    assert "require '/etc/phpmyadmin/config.inc.php';" in PMA
    assert "foreach (glob('/etc/phpmyadmin/conf.d/*.php')" in PMA
    assert "$cfg['TempDir'] = '/var/lib/phpmyadmin/tmp';" in PMA


def test_install_and_update_both_run_it_before_the_apt_cleanup():
    base = _function(INSTALL, "install_base_packages")
    pma_call = 'bash "${SCRIPT_DIR}/files/opanel-phpmyadmin.sh"'
    packages_call = 'bash "${SCRIPT_DIR}/files/opanel-packages.sh"'
    assert base.index(pma_call) < base.index(packages_call)
    tools = _function(UPDATE, "ensure_terminal_tools")
    pma_call = 'bash "$SOURCE_DIR/installer/files/opanel-phpmyadmin.sh"'
    packages_call = 'bash "$SOURCE_DIR/installer/files/opanel-packages.sh"'
    assert tools.index(pma_call) < tools.index(packages_call)
    # The self-healing sign-on file is regenerated right after, every update.
    assert tools.index(pma_call) < tools.index("refresh-tools")


def test_apt_phpmyadmin_and_apache_are_purged_only_once_upstream_is_in_place():
    universe = _function(PACKAGES, "remove_universe_leftovers")
    assert 'dpkg -s phpmyadmin' in universe and '[[ -f "$PMA_MARKER" ]]' in universe
    apache = _function(PACKAGES, "remove_apache_if_unused")
    assert "systemctl is-active --quiet apache2" in apache
    assert "libapache2-mod-php" in apache and "apt-mark manual ssl-cert" in apache
    assert "purge_only" in apache
    tail = PACKAGES[PACKAGES.rindex("remove_universe_leftovers"):]
    assert tail.index("remove_universe_leftovers") < tail.index("remove_apache_if_unused")


def test_php_mbstring_is_never_auto_removed():
    """WP-CLI runs under this same system php-cli (wp-site) and needs it; apt
    only ever saw phpmyadmin depending on it, so autoremove would offer it."""
    assert "php-mbstring" not in PACKAGES.split("LEFTOVERS_RE=", 1)[1].split("\n", 1)[0]


def test_a_phpmyadmin_reinstall_does_not_leave_single_sign_on_broken():
    """opanel-signon.php lives inside /usr/share/phpmyadmin, which a version
    bump replaces wholesale: refresh_tools_ols writes it fresh every time
    instead of sed-patching a file that might not exist any more (found
    switching off the Debian package, 2026-10-18)."""
    body = _function(HELPER, "refresh_tools_ols", "configure_unattended_upgrades")
    assert "cat >/usr/share/phpmyadmin/opanel-signon.php <<'SIGNON'" in body
    assert "__opanel_PMA_SIGNON_SECRET__" in body
    assert 'sed -i "s#__opanel_PMA_SIGNON_SECRET__#${pma_signon_secret}#" /usr/share/phpmyadmin/opanel-signon.php' in body
    # conf.d/opanel-signon.php sits outside that folder and keeps being patched
    # in place, not rewritten (it carries no placeholders to regenerate from).
    assert "sed -i -E \"s#('secure' => )(true|false)#\\1${pma_secure}#\" /etc/phpmyadmin/conf.d/opanel-signon.php" in body
    assert "/etc/phpmyadmin/conf.d/opanel-signon.php /usr/share/phpmyadmin/opanel-signon.php" not in body


def test_fix_phpmyadmin_permissions_still_covers_both_signon_files():
    body = _function(HELPER, "fix_phpmyadmin_permissions")
    assert "/etc/phpmyadmin/conf.d/opanel-signon.php" in body
    assert "/usr/share/phpmyadmin/opanel-signon.php" in body
    assert "/etc/phpmyadmin/config.inc.php" in body
