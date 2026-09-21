"""open_basedir has to be rendered where PHP will actually read it.

The per-site value used to be written by the helper into
``/usr/local/lsws/lsphp<XX>/etc/php.d/opanel-<user>-<hash>-lsphp<XX>.conf``.
That never took effect, for two independent reasons:

  * ``etc/php.d`` is not the build's ini scan directory -- the scan directory is
    ``etc/php/<ver>/mods-available`` -- and
  * the scan directory only loads ``*.ini`` while those fragments are ``*.conf``.

Verified on a live multi-tenant host: ``php -i`` reported
``open_basedir => string(0) ""`` on every installed LSPHP build while eighteen
such fragments sat unread, and one site's Linux user could read another site's
``wp-config.php``.

Moving the file would not have been a fix either. That directory is shared by
every site on the PHP version, so a per-site value there is read by every site
or by none. It belongs in the vhost's own ``phpIniOverride`` block, which is
scoped to one virtual host by construction.
"""
from __future__ import annotations

from pathlib import Path

from app.services import openlitespeed

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh"

ROOT_PATH = "/home/op_example_a1b2c3d4/example.test"
LINUX_USER = "op_example_a1b2c3d4"


def _render(**kwargs) -> str:
    return openlitespeed.render_vhost(
        "example.test", ROOT_PATH, linux_user=LINUX_USER, **kwargs
    )


def test_php_vhost_confines_the_site_with_open_basedir() -> None:
    conf = _render(app_type="php", php_version="8.4")
    assert "phpIniOverride" in conf
    assert f"php_admin_value   open_basedir {ROOT_PATH}:" in conf
    assert f"/var/lib/php/sessions/{LINUX_USER}" in conf
    assert f"/var/lib/php/uploads/{LINUX_USER}" in conf
    assert "/usr/share/php" in conf


def test_wordpress_vhost_confines_the_site_too() -> None:
    conf = _render(app_type="wordpress", php_version="8.4")
    assert f"php_admin_value   open_basedir {ROOT_PATH}:" in conf


def test_session_and_upload_dirs_are_per_site() -> None:
    conf = _render(app_type="php", php_version="8.4")
    assert f"php_admin_value   session.save_path /var/lib/php/sessions/{LINUX_USER}" in conf
    assert f"php_admin_value   upload_tmp_dir /var/lib/php/uploads/{LINUX_USER}" in conf


def test_open_basedir_does_not_name_another_site() -> None:
    """The confinement is worthless if it lists a sibling's tree."""
    conf = _render(app_type="php", php_version="8.4")
    line = next(l for l in conf.splitlines() if "open_basedir" in l)
    paths = line.split("open_basedir", 1)[1].strip().split(":")
    assert paths[0] == ROOT_PATH
    for path in paths:
        assert path in {
            ROOT_PATH,
            f"/var/lib/php/sessions/{LINUX_USER}",
            f"/var/lib/php/uploads/{LINUX_USER}",
            "/usr/share/php",
        }, f"unexpected path in open_basedir: {path}"


def test_static_site_gets_no_php_confinement() -> None:
    """No PHP runs, so there is nothing to confine and no stale directive."""
    conf = _render(app_type="static", php_version=None)
    assert "open_basedir" not in conf


def test_a_legacy_row_is_resolved_rather_than_downgraded_to_www_data() -> None:
    """A NULL linux_user must not make the tenant's PHP run as www-data.

    This test previously asserted the opposite shape -- open_basedir present,
    session.save_path absent -- and so codified two bugs at once. www-data is a
    member of every panel user's private group, so with /home/<user> at 0750
    that uid traverses into every tenant's tree; and emitting open_basedir while
    falling back to the system session path (now 0751 root:root) broke
    session_start() on exactly those sites. The site root encodes the uid
    (/home/<linux-user>/<domain>), so it is recovered from there instead.
    """
    conf = openlitespeed.render_vhost(
        "example.test", ROOT_PATH, app_type="php", php_version="8.4", linux_user=None
    )
    assert f"extUser               {LINUX_USER}" in conf
    assert "extUser               www-data" not in conf
    assert "/var/lib/php/sessions/www-data" not in conf
    assert f"php_admin_value   session.save_path /var/lib/php/sessions/{LINUX_USER}" in conf
    assert f"php_admin_value   open_basedir {ROOT_PATH}:" in conf


def test_a_root_path_outside_home_gets_no_shared_session_dir() -> None:
    """Nothing to derive, so no per-user dirs -- and no www-data session path."""
    conf = openlitespeed.render_vhost(
        "example.test", "/srv/elsewhere/example.test",
        app_type="php", php_version="8.4", linux_user=None,
    )
    assert "/var/lib/php/sessions/www-data" not in conf
    assert "session.save_path" not in conf


def test_helper_no_longer_writes_php_ini_keys_into_the_unread_pool_file() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    pool = helper[helper.index("ensure_php_pool()") : helper.index("ensure_php_runtime_dirs()")]
    for key in ("open_basedir", "upload_tmp_dir", "session.save_path"):
        assert f"{key} =" not in pool, (
            f"{key} is written into the LSPHP build's php.d directory, which is "
            "not scanned and would not be per-site even if it were"
        )


def test_helper_asserts_the_shared_php_runtime_parents() -> None:
    """php-common ships /var/lib/php/sessions world-writable (1733)."""
    helper = HELPER.read_text(encoding="utf-8")
    assert "install -d -o root -g root -m 0751 /var/lib/php/sessions" in helper
    assert "install -d -o root -g root -m 0751 /var/lib/php/uploads" in helper
    assert "chmod 0751 /var/lib/php/sessions /var/lib/php/uploads" in helper


def test_phpmyadmin_sessions_are_not_in_the_shared_parent() -> None:
    """Those sessions carry a cleartext database user and password."""
    install = (PROJECT_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    assert "session_save_path('/var/lib/php/pma-sessions')" in install
    assert "session_save_path('/var/lib/php/sessions')" not in install
    assert "'/var/lib/php/sessions'" not in install


def test_signon_secret_is_not_world_readable() -> None:
    """Every site's Linux user could read the gate secret at 0644."""
    install = (PROJECT_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8")
    assert "chmod 0640 /usr/share/phpmyadmin/opanel-signon.php" in install
    assert "chmod 0640 /etc/phpmyadmin/conf.d/opanel-signon.php" in install
    assert "chmod 644 /usr/share/phpmyadmin/opanel-signon.php" not in install
    assert "chmod 644 /etc/phpmyadmin/conf.d/opanel-signon.php" not in install
    assert "chmod 644" not in helper[
        helper.index("fix_phpmyadmin_permissions()") : helper.index("ols_disable_conflicting_apache()")
    ]
