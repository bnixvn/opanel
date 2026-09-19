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
from app.schemas.schemas import UserRestoreBatch
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
    account for."""
    source = inspect.getsource(backup.list_all_restorable_backups)

    assert "except Exception as exc" in source
    assert '"valid": False' in source


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

    assert "for index, backup_file in enumerate(backup_files" in source
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
    source = inspect.getsource(maintenance.restore_user_backups)

    assert "ensure_role(current_user.role, Role.admin)" in source
    assert "Select at least one backup" in source


def test_the_batch_size_is_bounded():
    import pytest
    from pydantic import ValidationError

    assert UserRestoreBatch(backup_files=["a.tar.gz"]).backup_files == ["a.tar.gz"]
    with pytest.raises(ValidationError):
        UserRestoreBatch(backup_files=[])
    with pytest.raises(ValidationError):
        UserRestoreBatch(backup_files=[f"{n}.tar.gz" for n in range(51)])


def test_the_batch_is_recorded():
    assert '"restore_user_batch"' in inspect.getsource(maintenance.restore_user_backups)
