"""The panel must reach a home created after opanel-api started.

Group membership reaches a process only at start, so a user a DA import
created mid-run left the running API locked out of /home/<user> and the Users
page answering 500 until someone restarted it.
"""

from pathlib import Path

from app.services import storage_quota

INSTALLER = Path(__file__).resolve().parents[3] / "installer"
HELPER = (INSTALLER / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
UPDATE = (INSTALLER / "update.sh").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    return source[start: source.index("\n}\n", start)]


def test_helper_grants_the_panel_an_acl_after_clearing_the_home():
    body = _function(HELPER, "ensure_panel_user_home")
    assert body.index('clear_path_acl "$home_dir"') < body.index('grant_panel_home_access "$home_dir"')
    grant = _function(HELPER, "grant_panel_home_access")
    assert 'setfacl -m u:opanel:r-x "$1"' in grant


def test_update_loop_keeps_the_same_acl_it_clears():
    block = UPDATE[UPDATE.index('setfacl -b -k "$home_dir"'):][:400]
    assert 'setfacl -m u:opanel:r-x "$home_dir"' in block


def test_the_grant_is_read_only():
    # sshd refuses a ChrootDirectory anyone but root can write to.
    assert "u:opanel:rw" not in HELPER + UPDATE
    assert "u:opanel:rwx" not in HELPER + UPDATE


def test_an_unreachable_site_root_does_not_fail_the_usage_walk(monkeypatch, tmp_path):
    locked = str(tmp_path / "locked")
    real_exists = Path.exists

    def exists(self, *args, **kwargs):
        if str(self) == locked:
            raise PermissionError(13, "Permission denied", locked)
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", exists)
    assert storage_quota._du_bytes([locked]) == 0
