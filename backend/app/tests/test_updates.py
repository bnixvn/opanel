"""The Updates page has to be able to say that a release is waiting.

`git ls-remote` returns a commit and no file content. The check assigned
`latest_version = current_version` and returned the literal `None` for
`update_available`, so the page could report which branch it tracked and
nothing else: twenty-two commits and a version bump sat on the branch and the
panel showed no update, because no code anywhere compared anything.

Two comparisons now exist. The commit is exact, and a box has one once it has
been updated by a copy of update.sh new enough to record it; update.sh applies
its own edits one run late, so versions are the fallback until then. The third
state is the one that matters most: when neither comparison can be made the
answer is None, never False -- reporting "up to date" out of ignorance is the
original failure.
"""
import json
import pathlib

from app.services import updates


class Completed:
    returncode = 0
    stdout = "0123456789abcdef0123456789abcdef01234567\trefs/heads/main\n"
    stderr = ""


def test_panel_status_checks_main_branch(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updates, "UPDATE_STATE_FILE", tmp_path / "update-status.json")
    monkeypatch.setattr(updates, "UPDATE_BRANCH", "main")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Completed()

    monkeypatch.setattr(updates.subprocess, "run", fake_run)

    result = updates.panel_release_status(force_refresh=True)

    assert result["update_channel"] == "branch"
    assert result["update_branch"] == "main"
    assert result["latest_tag"] == "origin/main"
    assert result["latest_commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert calls[0][0][:3] == ["git", "ls-remote", "--heads"]
    assert calls[0][0][-1] == "refs/heads/main"
    assert "--tags" not in calls[0][0]


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------
def test_ten_sorts_above_nine():
    """The bump that exposed this was 1.9.8 -> 1.10.0, which string comparison
    ranks backwards."""
    assert updates._version_tuple("1.10.0") > updates._version_tuple("1.9.8")
    assert "1.10.0" < "1.9.8", "string comparison really does get it wrong"


def test_a_version_that_is_not_a_number_yields_no_ordering():
    assert updates._version_tuple("") == ()
    assert updates._version_tuple("nightly") == ()


# --------------------------------------------------------------------------
# which comparison is used
# --------------------------------------------------------------------------
def test_the_commit_decides_when_the_box_recorded_one():
    assert updates._update_available("aaa", "bbb", "1.10.0", "1.10.0") is True, (
        "same version, different commit -- the branch has moved and the commit "
        "is the only thing that can see it"
    )
    assert updates._update_available("aaa", "aaa", "1.9.8", "1.10.0") is False, (
        "the installed commit IS the branch tip; a stale version reading must "
        "not override that"
    )


def test_versions_are_the_fallback_for_a_box_with_no_recorded_commit():
    assert updates._update_available("", "bbb", "1.9.8", "1.10.0") is True
    assert updates._update_available("", "bbb", "1.10.0", "1.10.0") is False


def test_an_unreadable_remote_version_is_unknown_not_up_to_date():
    assert updates._update_available("", "bbb", "1.9.8", "") is None, (
        "no commit recorded and no version read means the check learned "
        "nothing -- returning False here is what hid the release"
    )
    assert updates._update_available("", "", "1.9.8", "") is None


def test_a_box_ahead_of_the_branch_is_not_offered_a_downgrade():
    assert updates._update_available("", "bbb", "1.11.0", "1.10.0") is False


# --------------------------------------------------------------------------
# reading VERSION off the branch
# --------------------------------------------------------------------------
def test_the_remote_version_read_is_blobless_and_shallow(monkeypatch):
    argvs = []

    def fake_run(argv, **kwargs):
        argvs.append(argv)

        class R:
            returncode = 0
            stdout = "1.10.0\n" if "cat-file" in argv else ""
            stderr = ""

        return R()

    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    assert updates._remote_version("abc123") == "1.10.0"

    fetch = next(a for a in argvs if "fetch" in a)
    assert "--depth=1" in fetch and "--filter=blob:none" in fetch, (
        "a status check must not pay for a full clone"
    )
    assert any("cat-file" in a and "FETCH_HEAD:VERSION" in " ".join(a) for a in argvs)


def test_a_blob_that_is_not_a_version_is_refused(monkeypatch):
    def fake_run(argv, **kwargs):
        class R:
            returncode = 0
            # What ls-remote output looks like, i.e. what a wrong wiring returns.
            stdout = "0123456789abcdef0123456789abcdef01234567\trefs/heads/main\n"
            stderr = ""

        return R()

    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    assert updates._remote_version("abc123") == "", (
        "garbage must become 'unknown', never a release number on the page"
    )


def test_a_failed_fetch_is_unknown_rather_than_an_exception(monkeypatch):
    def fake_run(argv, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "network unreachable"

        return R()

    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    assert updates._remote_version("abc123") == ""


def test_no_commit_means_no_fetch_at_all(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("must not shell out without a commit to read")

    monkeypatch.setattr(updates.subprocess, "run", explode)
    assert updates._remote_version("") == ""


# --------------------------------------------------------------------------
# end to end through panel_release_status
# --------------------------------------------------------------------------
def _status(monkeypatch, tmp_path, *, remote_version, state=None):
    state_file = tmp_path / "update-status.json"
    if state is not None:
        state_file.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(updates, "UPDATE_STATE_FILE", state_file)
    monkeypatch.setattr(
        updates, "_latest_branch_ref_from_git", lambda: ("origin/main", "b" * 40)
    )
    monkeypatch.setattr(updates, "_remote_version", lambda commit: remote_version)
    return updates.panel_release_status(force_refresh=True)


def test_a_newer_version_on_the_branch_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "APP_VERSION", "1.9.8")
    result = _status(monkeypatch, tmp_path, remote_version="1.10.0")
    assert result["update_available"] is True
    assert result["latest_version"] == "1.10.0", (
        "the page needs the number to show, and it must come from the branch "
        "rather than being the installed one echoed back"
    )
    assert result["current_version"] == "1.9.8"


def test_a_recorded_commit_matching_the_tip_reads_as_up_to_date(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "APP_VERSION", "1.10.0")
    result = _status(
        monkeypatch, tmp_path, remote_version="1.10.0",
        state={"installed_commit": "b" * 40},
    )
    assert result["update_available"] is False
    assert result["installed_commit"] == "b" * 40


def test_a_recorded_commit_behind_the_tip_reports_an_update(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "APP_VERSION", "1.10.0")
    result = _status(
        monkeypatch, tmp_path, remote_version="1.10.0",
        state={"installed_commit": "a" * 40},
    )
    assert result["update_available"] is True, (
        "same version but a moved branch is still an update"
    )


def test_an_unreadable_branch_version_leaves_the_page_saying_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "APP_VERSION", "1.9.8")
    result = _status(monkeypatch, tmp_path, remote_version="")
    assert result["update_available"] is None
    assert result["latest_version"] == "1.9.8", (
        "the display falls back to the installed version so the row is not "
        "blank, but that fallback must not feed the comparison"
    )


def test_update_available_is_no_longer_a_constant(monkeypatch, tmp_path):
    """It returned the literal None regardless of input for the whole life of
    the feature; pin that at least two different answers are reachable."""
    monkeypatch.setattr(updates, "APP_VERSION", "1.9.8")
    behind = _status(monkeypatch, tmp_path, remote_version="1.10.0")
    monkeypatch.setattr(updates, "APP_VERSION", "1.10.0")
    level = _status(monkeypatch, tmp_path, remote_version="1.10.0")
    assert {behind["update_available"], level["update_available"]} == {True, False}


# --------------------------------------------------------------------------
# the installer half: nothing to compare unless update.sh records it
# --------------------------------------------------------------------------
def _update_sh() -> str:
    path = pathlib.Path(updates.__file__).resolve().parents[3] / "installer" / "update.sh"
    return path.read_text(encoding="utf-8")


def test_update_sh_captures_the_commit_it_installed():
    source = _update_sh()
    assert 'INSTALLED_COMMIT="$(git rev-parse HEAD' in source, (
        "/opt/opanel has no .git, so unless update.sh records the commit there "
        "is nothing on the box to compare against the branch tip"
    )


def test_update_sh_writes_the_commit_into_the_state_file():
    source = _update_sh()
    assert '"${INSTALLED_COMMIT:-}"' in source, "the commit must reach the python heredoc"
    assert 'state["installed_commit"] = installed_commit' in source
    # The heredoc reads argv positionally; an argument added without widening
    # the slice would be silently dropped.
    assert "installed_commit = sys.argv[8] if len(sys.argv) > 8 else \"\"" in source


def test_the_panel_reads_the_key_update_sh_writes():
    """Two files, one name. A rename on either side would quietly restore the
    'no update ever appears' behaviour with every test still green."""
    assert 'state.get("installed_commit")' in pathlib.Path(updates.__file__).read_text(
        encoding="utf-8"
    )
    assert 'state["installed_commit"]' in _update_sh()
