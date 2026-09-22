"""fix_site_tree's mode pass must not chmod through a symlink.

`find -type d -exec chmod 755 {} +` selects correctly -- find lstats, so it
never matches a symlink -- but the batched chmod re-resolves each path by name,
and chmod always dereferences. The site user owns these directories, so
replacing an enumerated entry with a symlink between the walk and the exec made
root chmod a path of their choosing: `chmod 644 /etc/shadow`, `chmod 755 /root`.
Reachable from every file-manager mutation through site-path-fix, and from
site-runtime-ensure.

The walk is extracted from the helper and run for real. It needs dir_fd, which
is POSIX-only, so it is skipped on the Windows development host -- it was
verified by hand on the Linux test box, and this keeps it verified in CI.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")

posix_only = pytest.mark.skipif(
    os.open not in getattr(os, "supports_dir_fd", set()),
    reason="the walk uses dir_fd, which this platform does not support",
)


def _extract_walk() -> str:
    marker = 'python3 - "$target" <<\'TREEPY\'\n'
    start = HELPER.index(marker) + len(marker)
    return HELPER[start : HELPER.index("\nTREEPY\n", start)]


@pytest.fixture
def walk(tmp_path):
    script = tmp_path / "treewalk.py"
    script.write_text(_extract_walk(), encoding="utf-8")

    def _run(root: Path):
        done = subprocess.run([sys.executable, str(script), str(root)],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return _run


@posix_only
def test_real_entries_get_the_standard_modes(walk, tmp_path):
    root = tmp_path / "site"
    (root / "public_html" / "sub").mkdir(parents=True)
    (root / "public_html" / "index.php").write_text("x", encoding="utf-8")
    (root / "public_html" / "sub" / "deep.txt").write_text("x", encoding="utf-8")
    os.chmod(root / "public_html", 0o777)
    os.chmod(root / "public_html" / "index.php", 0o600)

    walk(root)

    assert (root / "public_html").stat().st_mode & 0o777 == 0o755
    assert (root / "public_html" / "sub").stat().st_mode & 0o777 == 0o755
    assert (root / "public_html" / "index.php").stat().st_mode & 0o777 == 0o644
    assert (root / "public_html" / "sub" / "deep.txt").stat().st_mode & 0o777 == 0o644


@posix_only
def test_a_symlinked_file_does_not_get_its_target_chmodded(walk, tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    outside = tmp_path / "canary.txt"
    outside.write_text("secret", encoding="utf-8")
    os.chmod(outside, 0o600)
    os.symlink(outside, root / "evil.txt")

    walk(root)

    assert outside.stat().st_mode & 0o777 == 0o600, (
        "root followed a symlink out of the site tree and chmodded the target"
    )


@posix_only
def test_a_symlinked_directory_does_not_get_its_target_chmodded(walk, tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    outside = tmp_path / "canary_dir"
    outside.mkdir()
    os.chmod(outside, 0o700)
    os.symlink(outside, root / "evildir", target_is_directory=True)

    walk(root)

    assert outside.stat().st_mode & 0o777 == 0o700


@posix_only
def test_a_symlink_does_not_stop_the_rest_of_the_walk(walk, tmp_path):
    root = tmp_path / "site"
    (root / "public_html").mkdir(parents=True)
    outside = tmp_path / "canary.txt"
    outside.write_text("secret", encoding="utf-8")
    os.chmod(outside, 0o600)
    os.symlink(outside, root / "public_html" / "aaa_evil.txt")
    real = root / "public_html" / "zzz_real.txt"
    real.write_text("x", encoding="utf-8")
    os.chmod(real, 0o600)

    walk(root)

    assert real.stat().st_mode & 0o777 == 0o644, "a skipped symlink aborted the walk"
    assert outside.stat().st_mode & 0o777 == 0o600


def test_the_shell_no_longer_chmods_by_name():
    """Runs everywhere, including the Windows dev host."""
    tree = HELPER[HELPER.index("fix_site_tree() {"):]
    tree = tree[: tree.index("require_ip_or_cidr()")]
    assert "O_NOFOLLOW" in tree and "os.fchmod" in tree
    assert "-exec chmod 755" not in tree
    assert "-exec chmod 644" not in tree
