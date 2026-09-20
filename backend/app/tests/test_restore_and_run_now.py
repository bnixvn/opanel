"""Running a schedule early, and restoring several archives in one go.

Both exist because the panel could only wait: a schedule ran when its cron
said so, and a restore was one archive at a time from whichever folder the
operator happened to know about.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.api import maintenance
from app.schemas.schemas import UserRestoreBatch, UserRestoreDescribe
from app.services import backup, backup_scheduler


# --------------------------------------------------------------------------
# Run now
# --------------------------------------------------------------------------

def test_run_now_goes_through_the_schedule_itself():
    """Not a second kind of backup: the same slot, the same retention, the
    same recorded status."""
    source = inspect.getsource(maintenance._run_schedule_now_job)

    assert "backup_scheduler.run_schedule(db, schedule" in source
    assert "backup.create_user_backup" not in source


def test_run_now_is_queued_not_awaited():
    """A full run of every account takes minutes; holding the request open for
    it would time out."""
    source = inspect.getsource(maintenance.run_backup_schedule_now)

    assert "_backup_job_executor.submit(_run_schedule_now_job" in source
    assert "_queue_backup_job" in source


def test_run_now_is_admin_only_and_checks_the_schedule_exists():
    source = inspect.getsource(maintenance.run_backup_schedule_now)

    assert "ensure_role(current_user.role, Role.admin)" in source
    assert "Backup schedule not found" in source


def test_run_now_reports_progress_per_account():
    source = inspect.getsource(maintenance._run_schedule_now_job)

    assert "on_progress=progress" in source
    assert "index}/{total}" in source or "{index} of {total}" in source


def test_run_now_passes_the_schedules_own_verdict_through():
    """A run that skipped an unreadable file still ran; flattening it to
    done/error would lose that."""
    source = inspect.getsource(maintenance._run_schedule_now_job)

    assert "schedule.last_message" in source


def test_run_now_is_recorded():
    assert '"run_backup_schedule"' in inspect.getsource(maintenance.run_backup_schedule_now)


# --------------------------------------------------------------------------
# Restore list and batch
# --------------------------------------------------------------------------

def test_the_restore_list_covers_both_places_a_backup_can_live():
    source = inspect.getsource(backup.list_all_restorable_backups)

    assert "_user_restore_dir()" in source
    assert 'Path(settings.backup_root) / "users"' in source
    assert '"uploaded"' in source and '"account"' in source


def test_the_restore_list_skips_the_folders_that_are_not_accounts():
    source = inspect.getsource(backup.list_all_restorable_backups)

    assert '{"restore", "uploads"}' in source


def test_an_archive_that_cannot_be_described_still_appears():
    """It has to be visible to be deleted; hiding it leaves disk nobody can
    account for. The listing never opens one, so it cannot fail there at all;
    describing is where a bad archive shows up, and it is reported, not
    raised."""
    listing = inspect.getsource(backup.list_all_restorable_backups)
    assert "raise" not in listing

    describing = inspect.getsource(backup.describe_backups)
    assert "except Exception as exc" in describing
    assert '"valid": False' in describing


def test_the_same_file_is_never_listed_twice():
    source = inspect.getsource(backup.list_all_restorable_backups)

    assert "seen" in source


def test_the_route_serves_the_combined_list():
    source = inspect.getsource(maintenance.list_user_restore_backups)

    assert "backup.list_all_restorable_backups()" in source


def test_a_batch_restore_runs_them_one_after_another():
    """Each restore rewrites site files, imports databases and reloads
    OpenLiteSpeed; in parallel they would fight over the web server."""
    source = inspect.getsource(maintenance._run_restore_batch_job)

    assert "for index, item in enumerate(items" in source
    assert "submit" not in source


def test_one_failing_archive_does_not_abandon_the_rest():
    source = inspect.getsource(maintenance._run_restore_batch_job)

    assert "failures.append" in source
    assert "continue" in source


def test_every_path_is_checked_before_anything_is_restored():
    """Refusing halfway through would leave some accounts restored and some
    not, from one click."""
    source = inspect.getsource(maintenance.restore_user_backups)
    check = source.index("backup.user_backup_path(backup_file)")
    submit = source.index("_backup_job_executor.submit")

    assert check < submit


def test_a_batch_restore_is_admin_only_and_needs_a_selection():
    import pytest
    from pydantic import ValidationError

    assert "ensure_role(current_user.role, Role.admin)" in inspect.getsource(maintenance.restore_user_backups)
    # An empty selection is refused by the payload, before the route runs.
    with pytest.raises(ValidationError, match="Select at least one backup"):
        UserRestoreBatch()


def test_the_batch_size_is_bounded():
    import pytest
    from pydantic import ValidationError

    assert UserRestoreBatch(backup_files=["a.tar.gz"]).backup_files == ["a.tar.gz"]
    with pytest.raises(ValidationError):
        UserRestoreBatch(backup_files=[], remote_items=[])
    with pytest.raises(ValidationError):
        UserRestoreBatch(backup_files=[f"{n}.tar.gz" for n in range(51)])
    # The cap is on the whole selection, not on each half.
    with pytest.raises(ValidationError):
        UserRestoreBatch(backup_files=[f"{n}.tar.gz" for n in range(30)],
                         remote_items=[{"target_id": 1, "key": f"{n}.tar.gz"} for n in range(30)])


def test_a_selection_may_be_entirely_remote():
    payload = UserRestoreBatch(remote_items=[{"target_id": 1, "key": "acme/acme-monday.tar.gz"}])

    assert payload.backup_files == []
    assert payload.remote_items[0].key == "acme/acme-monday.tar.gz"


def test_the_panel_downloads_a_remote_archive_itself():
    """The operator picked a backup, not a download."""
    source = inspect.getsource(maintenance._run_restore_batch_job)

    assert "_fetch_remote_archive(" in source
    assert 'if not backup_file:' in source


def test_a_fetched_copy_is_removed_once_it_has_been_restored():
    """The destination still holds it; a 5 GB archive left in the restore
    folder is disk nobody asked for."""
    source = inspect.getsource(maintenance._run_restore_batch_job)

    assert "if fetched:" in source
    assert "Path(fetched).unlink(missing_ok=True)" in source
    # In a finally, so a failed restore does not leak it either.
    assert "finally:" in source


def test_a_remote_target_is_checked_before_anything_starts():
    source = inspect.getsource(maintenance.restore_user_backups)
    check = source.index("Backup target {ref.target_id} not found")
    submit = source.index("_backup_job_executor.submit")

    assert check < submit


def test_there_is_no_separate_fetch_step():
    """Downloading is the panel's business; it was a step handed to the
    operator for no reason."""
    assert not hasattr(maintenance, "fetch_remote_restore_backup")
    assert not hasattr(maintenance, "_run_remote_fetch_job")


def test_describe_keeps_its_own_payload():
    """It takes only local paths, and many more of them than a restore would."""
    assert UserRestoreDescribe.model_fields["backup_files"].metadata


def test_the_batch_is_recorded():
    assert '"restore_user_batch"' in inspect.getsource(maintenance.restore_user_backups)


# --------------------------------------------------------------------------
# Listing must not read archives
# --------------------------------------------------------------------------

def test_the_listing_opens_no_archive():
    """Finding a manifest in an archive written before the manifest moved to
    the front means decompressing all of it: 159 seconds for the seventeen on
    the production box, just to show filenames."""
    source = inspect.getsource(backup.list_all_restorable_backups)

    assert "describe_user_backup" not in source
    assert "tarfile" not in source


def test_the_listing_takes_the_account_from_the_folder():
    """Which is what put it there, so it needs no manifest to know."""
    source = inspect.getsource(backup.list_all_restorable_backups)

    assert '_add(path, "account", account_dir.name)' in source


def test_undescribed_fields_are_null_not_zero():
    source = inspect.getsource(backup.list_all_restorable_backups)

    assert '"websites": (cached or {}).get("websites")' in source
    assert '"valid": (cached or {}).get("valid")' in source


def test_describing_is_cached_on_identity_not_path():
    """A rotation slot is overwritten in place, so the path alone would serve
    a stale description of a different archive."""
    source = inspect.getsource(backup._archive_identity)

    assert "st_mtime" in source and "st_size" in source


def test_the_describe_cache_is_bounded():
    assert "len(_describe_cache) > 500" in inspect.getsource(backup.describe_backups)


def test_a_missing_file_describes_as_invalid_rather_than_raising():
    source = inspect.getsource(backup.describe_backups)

    assert "Backup not found" in source
    assert "continue" in source


# --------------------------------------------------------------------------
# What is on the destination
# --------------------------------------------------------------------------

def test_the_remote_listing_never_downloads():
    source = inspect.getsource(maintenance.list_remote_restore_backups)

    assert "list_s3_backups" in source
    assert "download_from_s3" not in source


def test_one_failing_destination_does_not_hide_the_others():
    source = inspect.getsource(maintenance.list_remote_restore_backups)

    assert "errors.append" in source
    assert "continue" in source


def test_the_account_comes_from_the_key_folder():
    source = inspect.getsource(maintenance.list_remote_restore_backups)

    assert 'tail.split("/")[0] if "/" in tail else ""' in source


def test_a_fetched_key_cannot_place_a_file_outside_the_restore_folder():
    """A key is remote input."""
    source = inspect.getsource(backup.download_from_s3)

    assert "Path(str(key).replace" in source and ").name" in source
    assert "folder not in destination.parents" in source
    assert 'endswith(".tar.gz")' in source


def test_a_half_downloaded_archive_is_never_offered():
    source = inspect.getsource(backup.download_from_s3)

    assert "with_suffix" in source and "os.replace(partial, destination)" in source
    assert "partial.unlink(missing_ok=True)" in source


def test_pulling_an_object_down_is_admin_only_by_being_part_of_the_restore():
    """There is no standalone fetch any more, so the only way to pull an
    object down is through the restore route, which is admin-only."""
    assert "ensure_role(current_user.role, Role.admin)" in inspect.getsource(maintenance.restore_user_backups)
    assert "_fetch_remote_archive" in inspect.getsource(maintenance._run_restore_batch_job)


# --------------------------------------------------------------------------
# One place for local backups
# --------------------------------------------------------------------------

def test_every_kind_of_backup_has_its_own_folder(tmp_path, monkeypatch):
    """A website archive used to drop its folder at the root, beside users/
    and db-snapshots/, so the layout held only as long as no domain was
    called one of those."""
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path))

    assert backup._user_backup_dir("acme") == tmp_path / "users" / "acme"
    assert backup._site_backup_dir("example.test") == tmp_path / "sites" / "example.test"
    assert backup._user_restore_dir() == tmp_path / "restore"


def test_a_domain_cannot_escape_the_backup_root(tmp_path, monkeypatch):
    """It reaches this helper from an upload form as well as from a row."""
    import pytest

    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path))

    for bad in ("../etc", "a/../..", "", "   ", "not a domain", "/abs"):
        with pytest.raises(ValueError, match="Invalid domain"):
            backup._site_backup_dir(bad)


def test_a_site_named_users_no_longer_collides(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path))

    assert backup._site_backup_dir("users.com") != backup._user_backup_dir("acme").parent
    assert "sites" in backup._site_backup_dir("users.com").parts


def test_uploads_from_older_releases_are_still_found():
    """They were written to users/restore and users/uploads."""
    source = inspect.getsource(backup.list_uploaded_user_backups)

    assert '"users" / "restore"' in source
    assert '"users" / "uploads"' in source


def test_the_installer_and_the_updater_agree_on_the_layout():
    """An install-only change never reaches a running box, so both have to
    create the same three folders."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    install = (root / "installer" / "install.sh").read_text(encoding="utf-8")
    update = (root / "installer" / "update.sh").read_text(encoding="utf-8")

    assert "for sub in users sites restore; do" in install
    assert "for d in users sites restore; do" in update
    assert "migrate_backup_layout" in update


def test_the_migration_merges_rather_than_clobbers():
    """A name present on both sides is the same rotation slot, and the copy
    already in the new place is the newer one."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    update = (root / "installer" / "update.sh").read_text(encoding="utf-8")
    block = update[update.index("migrate_backup_layout() {"):update.index("migrate_drop_iframe_blocking() {")]

    assert "if target.exists():" in block and "continue" in block
    assert "RESERVED" in block
    # Only a folder that looks like a domain is treated as a site.
    assert '"." not in entry.name' in block
