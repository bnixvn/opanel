"""php2shell: PHP's own mitigation (operator, 2026-10-24).

session.upload_progress.enabled=0 everywhere, fatal_error_backtraces=0 on
8.5: in every lsphp's ini, and in every site's vhost as php_admin_value, which
a site's .user.ini or .htaccess cannot turn back on.
"""
from pathlib import Path

from app.services import openlitespeed

HELPER = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _function(name):
    start = HELPER.index(f"\n{name}() {{\n") + 1
    return HELPER[start:HELPER.index("\n}\n", start)]


def test_every_php_site_carries_it_as_an_admin_value():
    for app_type in ("php", "wordpress"):
        for version, backtraces in (("8.1", False), ("8.4", False), ("8.5", True)):
            vhost = openlitespeed.render_vhost("example.test", "/home/u/example.test", app_type=app_type, php_version=version)
            assert "php_admin_value   session.upload_progress.enabled 0" in vhost, (app_type, version)
            assert ("php_admin_value   fatal_error_backtraces 0" in vhost) is backtraces, (app_type, version)


def test_a_static_site_has_no_php_settings():
    vhost = openlitespeed.render_vhost("example.test", "/home/u/example.test", app_type="static")
    assert "upload_progress" not in vhost


def test_every_lsphp_gets_it_on_install_and_update():
    fn = _function("ensure_php2shell_ini")
    assert 'echo "session.upload_progress.enabled = 0"' in fn
    assert 'echo "fatal_error_backtraces = 0"' in fn and '"$ver" == 8.5' in fn
    assert "/usr/local/lsws/lsphp*/etc/php/*/mods-available" in fn
    # Workers restart only when something changed.
    assert "cmp -s" in fn and "restart_openlitespeed" in fn
    hygiene = HELPER.split("  log-hygiene)\n", 1)[1].split("\n    ;;\n", 1)[0]
    assert "ensure_php2shell_ini" in hygiene
    # A version installed later has it from the start.
    assert "ensure_php2shell_ini >/dev/null || true" in _function("install_php_version")
