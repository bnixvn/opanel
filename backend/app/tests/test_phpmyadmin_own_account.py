"""phpMyAdmin's PHP runs as an account of its own, never as the web server's.

www-data is a member of every panel user's private group (a home is 0750
root:<user>, and that is how the web server reaches a site). phpMyAdmin's
lsphp ran as www-data, with no open_basedir: checked on a live server on
2026-11-17, that PHP could read every tenant's wp-config.php. One flaw in
phpMyAdmin was every database password on the server.

It now runs as `opanel-pma`, a system account in no other group, and the files
that carry phpMyAdmin's secrets belong to that account's group.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
PMA = (ROOT / "installer" / "files" / "opanel-phpmyadmin.sh").read_text(encoding="utf-8")


def _function(text: str, name: str) -> str:
    start = text.index(f"\n{name}() {{\n") + 1
    return text[start:text.index("\n}\n", start) + 3]


def _tools_vhost(text: str, function: str) -> str:
    body = text[text.index(f"\n{function}() {{\n"):]
    start = body.index("extprocessor ")
    return body[start:body.index("rewrite  {", start)]


def test_the_tools_vhost_runs_phpmyadmin_as_its_own_account():
    for text, function in ((HELPER, "refresh_tools_ols"), (INSTALL, "write_tools_vhost_config")):
        vhost = _tools_vhost(text, function)
        assert "  extUser                 ${pma_user}\n" in vhost
        assert "  extGroup                ${pma_group}\n" in vhost
        assert "www-data" not in vhost
        # A name and a socket of its own: under the old ones an lsphp still
        # running as www-data would go on answering.
        assert vhost.startswith("extprocessor opanel-pma {\n")
        assert "  add                     lsapi:opanel-pma php\n" in vhost
        assert "lsapi:lsphp" not in vhost


def test_the_helper_and_the_installer_write_the_same_php_for_phpmyadmin():
    assert _tools_vhost(HELPER, "refresh_tools_ols") == _tools_vhost(INSTALL, "write_tools_vhost_config")
    for text in (HELPER, INSTALL):
        assert 'lsphp_sock="/tmp/lshttpd/opanel-pma-lsphp${default_ver_no_dot}.sock"' in text
        assert 'lsphp_sock="/tmp/lshttpd/lsphp${default_ver_no_dot}.sock"' not in text


def test_the_account_is_made_before_the_vhost_that_names_it():
    for text, function in ((HELPER, "refresh_tools_ols"), (INSTALL, "write_tools_vhost_config")):
        body = text[text.index(f"\n{function}() {{\n"):]
        assert body.index("ensure_pma_account\n") < body.index("extprocessor opanel-pma {")
        assert body.index('pma_user="$(pma_php_user)"') < body.index("extprocessor opanel-pma {")


def test_the_account_is_in_no_other_group():
    for text in (HELPER, INSTALL):
        body = _function(text, "ensure_pma_account")
        assert "useradd --system" in body
        assert "--shell /usr/sbin/nologin" in body
        # Taken out of every supplementary group, each time: a tenant's group
        # or opanel-sites would give back what the account takes away.
        assert 'usermod -G ""' in body
        assert "usermod -aG" not in body
        assert "-G opanel-sites" not in body


def test_phpmyadmin_keeps_working_where_the_account_cannot_be_made():
    for text in (HELPER, INSTALL):
        assert "echo www-data" in _function(text, "pma_php_user")
        # ... and then its secrets stay readable by www-data, through the
        # group it is in.
        assert "opanel" in _function(text, "pma_secrets_group")


def test_phpmyadmins_secrets_belong_to_the_accounts_group():
    for text in (HELPER, INSTALL):
        body = _function(text, "fix_phpmyadmin_permissions")
        assert body.index("ensure_pma_account") < body.index("pma_secrets_group")
        assert 'chgrp "$secrets_group" "$file"' in body
        assert 'chgrp "$opanel_SITES_GROUP"' not in body
        assert "chgrp opanel-sites" not in body
        assert "chmod 0640" in body
        # Its sessions and its working directory are the account's.
        assert 'install -d -o "$pma_user" -g "$pma_group" -m 0700 /var/lib/php/pma-sessions' in body
        assert 'chown -R "$pma_user":"$pma_group" /var/lib/phpmyadmin/tmp' in body
        assert "-o www-data" not in body


def test_sessions_another_account_wrote_are_removed():
    body = _function(HELPER, "fix_phpmyadmin_permissions")
    assert 'find /var/lib/php/pma-sessions -maxdepth 1 -type f ! -user "$pma_user" -delete' in body


def test_the_installer_no_longer_hands_the_signon_files_to_the_web_servers_group():
    # The shell lines after the sign-on secret is put into the file.
    setup = INSTALL[INSTALL.index('sed -i "s#__opanel_PMA_SIGNON_SECRET__#'):]
    setup = setup[:setup.index("\n}\n")]
    assert "chown root:opanel-sites" not in setup
    assert "chown root:www-data" not in setup
    assert "install -d -o www-data" not in setup
    assert setup.rstrip().endswith("fix_phpmyadmin_permissions")


def test_phpmyadmins_working_directory_follows_the_account():
    assert 'install -d -o www-data -g www-data -m 0770 "$PMA_TEMP_DIR"' not in PMA
    assert "id -u opanel-pma" in PMA
    assert 'install -d -o "$pma_owner" -g "$(id -gn "$pma_owner")" -m 0770 "$PMA_TEMP_DIR"' in PMA
