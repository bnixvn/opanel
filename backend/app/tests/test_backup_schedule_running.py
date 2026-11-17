"""A backup schedule shows as running while it runs, including from the timer.

The timer runs a schedule in a process of its own, with no job in the API for
the page to watch, so run_schedule keeps a heartbeat (running_since) that the
schedules endpoint turns into a live "running" flag. A run killed mid-way
leaves the heartbeat behind, so it goes stale on its own instead of showing as
running for ever.
"""
import inspect
from datetime import datetime, timedelta

from app.services import backup_scheduler
from app.schemas.schemas import BackupScheduleOut, BACKUP_SCHEDULE_RUNNING_FRESH_SECONDS


def test_run_schedule_marks_running_and_clears_it():
    src = inspect.getsource(backup_scheduler.run_schedule)
    assert "schedule.running_since = datetime.utcnow()" in src
    assert 'schedule.last_status = "running"' in src
    assert "schedule.running_since = None" in src


def test_run_schedule_keeps_a_heartbeat_through_the_run():
    src = inspect.getsource(backup_scheduler.run_schedule)
    assert "threading.Thread(target=_heartbeat_loop" in src
    assert "stop.set()" in src and "heartbeat.join" in src


def test_heartbeat_runs_on_its_own_session():
    src = inspect.getsource(backup_scheduler._heartbeat_loop)
    assert "SessionLocal()" in src and "running_since = datetime.utcnow()" in src


def _out(**kw):
    base = dict(id=1, user_ids=[1], schedule="0 2 * * *", retention=7,
                is_active=True, last_status="running")
    base.update(kw)
    return BackupScheduleOut(**base)


def test_a_fresh_heartbeat_reads_as_running():
    assert _out(running_since=datetime.utcnow()).running is True


def test_a_stale_heartbeat_is_not_running():
    old = datetime.utcnow() - timedelta(seconds=BACKUP_SCHEDULE_RUNNING_FRESH_SECONDS + 30)
    out = _out(running_since=old)
    assert out.running is False, "a killed run's heartbeat goes stale, not running for ever"


def test_no_heartbeat_is_not_running():
    assert _out(running_since=None).running is False
