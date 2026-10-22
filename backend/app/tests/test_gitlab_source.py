"""The panel updates from its own GitLab, and servers installed before the move follow.

The source moved from github.com/bnixvn to gitlab.sieunhim.com/bnix on
2026-10-22 (operator's request). Every server already installed has a source
checkout whose remote is the GitHub address, and the helper of the release
before the move hands that address to update.sh as REPO_URL. Both have to be
moved, or those servers keep fetching from GitHub until someone notices.
"""
from __future__ import annotations

import subprocess
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
UPDATE = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
UPDATES_PY = (ROOT / "backend" / "app" / "services" / "updates.py").read_text(encoding="utf-8")
GITLAB = "https://gitlab.sieunhim.com/bnix/opanel.git"
GITHUB = "https://github.com/bnixvn/opanel.git"


def test_every_default_points_at_gitlab():
    assert f'DEFAULT_REPO_URL="{GITLAB}"' in UPDATE
    assert f'"opanel_REPO_URL", "{GITLAB}"' in UPDATES_PY
    assert HELPER.count(f"${{REPO_URL:-{GITLAB}}}") == 3
    assert GITHUB not in HELPER and GITHUB not in UPDATES_PY
    assert 'MAIL_WEBMAIL_REPO="https://gitlab.sieunhim.com/bnix/webmail.git"' in HELPER
    assert "https://gitlab.sieunhim.com/bnix/opanel" in INSTALL


def test_an_old_github_repo_url_is_read_as_the_new_default():
    body = UPDATE[UPDATE.index("LEGACY_REPO_URLS=("):]
    body = body[:body.index("\ndone\n")]
    assert GITHUB in body and 'REPO_URL="$DEFAULT_REPO_URL"' in body


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")
def test_a_checkout_on_github_moves_and_any_other_remote_stays(tmp_path):
    function = UPDATE[UPDATE.index("ensure_git_remote() {"):]
    function = function[:function.index("\n}\n") + 3]
    legacy = UPDATE[UPDATE.index("LEGACY_REPO_URLS=("):].split("\n", 1)[0]
    script = tmp_path / "t.sh"
    script.write_text(
        "set -euo pipefail\nlog() { :; }\n"
        f'REPO_URL="{GITLAB}"\nGIT_REMOTE=origin\n{legacy}\n{function}'
        "cd \"$1\"\nensure_git_remote\ngit remote get-url origin\n",
        encoding="utf-8",
    )

    def remote_after(url):
        repo = tmp_path / f"repo{abs(hash(url))}"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)
        done = subprocess.run(["bash", str(script), str(repo)], capture_output=True, text=True, check=True)
        return done.stdout.strip()

    assert remote_after(GITHUB) == GITLAB
    assert remote_after("https://github.com/bnixvn/opanel") == GITLAB
    assert remote_after("https://example.com/fork.git") == "https://example.com/fork.git"


def test_release_archives_and_the_install_tarball_know_gitlab():
    release = UPDATE[UPDATE.index("release_archive_url() {"):]
    release = release[:release.index("\n}\n")]
    assert "https://gitlab.sieunhim.com/*)" in release and "/-/archive/" in release
    tarball = INSTALL[INSTALL.index("opanel_main_tarball_url() {"):]
    tarball = tarball[:tarball.index("\n}\n")]
    assert "/-/archive/main/" in tarball and "archive/refs/heads/main.tar.gz" in tarball
