"""A box can follow a branch other than main (the staging box follows staging).

The branch lives in backend/.env as opanel_UPDATE_BRANCH, and every update path
has to honour it: a bare `opanel-update`, the panel's Update button and the
auto-update timer. The button and the timer go through the root helper, where
sudo has already wiped any BRANCH the panel exported -- so the helper used to
hard-code main, and one click would drag a staging box back onto main.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[3] / "installer"
HELPER = (INSTALLER / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
UPDATE = (INSTALLER / "update.sh").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    return source[start: source.index("\n}\n", start)]


def test_the_helper_no_longer_hard_codes_main():
    for name in ("run_panel_update", "write_panel_auto_update_timer"):
        body = _function(HELPER, name)
        assert "BRANCH:-main" not in body, name
        assert "$(panel_update_branch)" in body, name


def test_the_helper_reads_and_validates_the_box_branch():
    body = _function(HELPER, "panel_update_branch")
    assert "env_get opanel_UPDATE_BRANCH" in body
    assert "git check-ref-format --branch" in body
    assert 'branch="${branch:-main}"' in body


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("env_line, preset, expected", [
    ('opanel_UPDATE_BRANCH="staging"\n', None, "staging"),
    ("opanel_UPDATE_BRANCH=staging\r\n", None, "staging"),
    ("PANEL_PORT=2222\n", None, "main"),
    ('opanel_UPDATE_BRANCH="staging"\n', "main", "main"),   # an explicit BRANCH still wins
])
def test_update_sh_defaults_to_the_box_branch(tmp_path, env_line, preset, expected):
    lines = [line for line in UPDATE.splitlines() if line.startswith(("_ENV_BRANCH=", "BRANCH="))]
    assert len(lines) == 2, lines
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_bytes(env_line.encode())
    script = "\n".join([f'APP_DIR="{tmp_path.as_posix()}"', *lines, 'printf "%s" "$BRANCH"'])
    env = {"PATH": __import__("os").environ.get("PATH", "")}
    if preset is not None:
        env["BRANCH"] = preset
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert result.stdout == expected, result.stderr


def test_the_panel_starts_with_the_branch_key_in_its_env_file(tmp_path):
    """An unknown key in .env is a start-up failure (extra_forbidden); the
    staging box's first update died on exactly this."""
    from app.core.config import Settings

    env_file = tmp_path / ".env"
    env_file.write_text("opanel_UPDATE_BRANCH=staging\n", encoding="utf-8")
    assert Settings(_env_file=str(env_file)).opanel_update_branch == "staging"
