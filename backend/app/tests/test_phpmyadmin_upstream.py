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


def test_apt_phpmyadmin_is_purged_before_our_own_copy_is_written():
    """Purging it afterwards, once the files underneath already changed,
    deleted every path its own manifest tracks regardless of their new
    content -- index.php and half of libraries/ and src/ gone, and its
    postrm emptied /etc/phpmyadmin outright (found live, 2026-10-18)."""
    assert "dpkg -s phpmyadmin" in PMA
    purge_at = PMA.index("apt-get -o DPkg::Lock::Timeout=120 purge -y phpmyadmin")
    write_at = min(PMA.index(marker) for marker in ('mv "$extracted" "$PMA_DIR"', "cat >/etc/phpmyadmin/config.inc.php"))
    assert purge_at < write_at
    assert "dpkg -s phpmyadmin" not in _function(PACKAGES, "remove_universe_leftovers")


def test_apache_is_purged_only_once_it_is_confirmed_unused():
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


def test_phpmyadmin_single_sign_on_self_heals_on_every_refresh():
    """Both signon files are written fresh every time refresh_tools_ols runs,
    rather than sed-patched assuming they exist: opanel-signon.php lives
    inside /usr/share/phpmyadmin, which a version bump replaces wholesale, and
    conf.d/opanel-signon.php sits in /etc/phpmyadmin, which purging the
    now-gone apt phpmyadmin package turned out to empty outright - both found
    broken switching off the Debian package, 2026-10-18."""
    body = _function(HELPER, "refresh_tools_ols", "configure_unattended_upgrades")
    assert "cat >/usr/share/phpmyadmin/opanel-signon.php <<'SIGNON'" in body
    assert "__opanel_PMA_SIGNON_SECRET__" in body
    assert 'sed -i "s#__opanel_PMA_SIGNON_SECRET__#${pma_signon_secret}#" /usr/share/phpmyadmin/opanel-signon.php' in body
    assert "cat >/etc/phpmyadmin/conf.d/opanel-signon.php <<PHP" in body
    assert "env_get PMA_BLOWFISH_SECRET" in body
    assert "\\$cfg['blowfish_secret'] = '${blowfish_secret}';" in body
    assert "install -d -m 0755 /etc/phpmyadmin/conf.d" in body
    # No more assuming either file was already there to patch in place.
    assert "sed -i -E \"s#('secure' => )(true|false)#" not in body


def test_the_blowfish_secret_is_persisted_not_regenerated_on_each_run():
    assert "PMA_BLOWFISH_SECRET=$(openssl rand -hex 32)" in INSTALL
    assert 'env_set_default PMA_BLOWFISH_SECRET "$(openssl rand -hex 32)"' in UPDATE
    setup = _function(INSTALL, "setup_phpmyadmin_sso")
    assert 'grep -oP "^PMA_BLOWFISH_SECRET=\\K.*"' in setup


def test_fix_phpmyadmin_permissions_still_covers_both_signon_files():
    body = _function(HELPER, "fix_phpmyadmin_permissions")
    assert "/etc/phpmyadmin/conf.d/opanel-signon.php" in body
    assert "/usr/share/phpmyadmin/opanel-signon.php" in body
    assert "/etc/phpmyadmin/config.inc.php" in body
