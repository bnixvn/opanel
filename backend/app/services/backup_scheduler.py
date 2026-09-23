from datetime import datetime, timedelta
import json
import logging

from app.core.database import SessionLocal
from app.core.secrets import decrypt
from app.models.entities import BackupSchedule, BackupTarget, User
from app.services import backup

logger = logging.getLogger(__name__)


def _field_matches(field: str, value: int) -> bool:
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = max(int(step_text or "1"), 1)
        if part == "*":
            start, end = 0, 59
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _cron_due(schedule: str, now: datetime) -> bool:
    minute, hour, day, month, weekday = schedule.split()
    cron_weekday = (now.weekday() + 1) % 7
    return (
        _field_matches(minute, now.minute)
        and _field_matches(hour, now.hour)
        and _field_matches(day, now.day)
        and _field_matches(month, now.month)
        and (_field_matches(weekday, cron_weekday) or (cron_weekday == 0 and _field_matches(weekday, 7)))
    )


# How far back a missed schedule is still worth running. Long enough to
# cover a reboot or a backup run that blocked the runner for hours, short
# enough that a box switched off for a month fires once on return, not thirty
# times.
CATCHUP_WINDOW_MINUTES = 26 * 60


def _due_since(schedule: str, since: datetime | None, now: datetime) -> bool:
    """Was this schedule due at any minute that has not been covered yet?

    The runner used to ask only whether the current minute matched. It does not
    get to look every minute: systemd restarts it a minute after the previous
    run *finished*, so the cycle drifts forward and whole minutes are never
    examined -- 23 of every 180 on the production box. Worse, a run that takes
    an hour blinds it to every other schedule for that hour. Either way a
    backup silently did not happen.

    So walk the minutes since the last run instead. A schedule that has never
    run has no window to catch up on and is judged on the current minute alone,
    or creating one would immediately fire it for a time it was never meant to
    cover.
    """
    if since is None:
        return _cron_due(schedule, now)
    start = since.replace(second=0, microsecond=0) + timedelta(minutes=1)
    earliest = now - timedelta(minutes=CATCHUP_WINDOW_MINUTES)
    if start < earliest:
        start = earliest
    moment = start
    while moment <= now:
        if _cron_due(schedule, moment):
            return True
        moment += timedelta(minutes=1)
    return False


def _s3_secret(target) -> str:
    try:
        return decrypt(target.s3_secret_key) if target.s3_secret_key else ""
    except RuntimeError:
        raise RuntimeError(
            "Failed to decrypt S3 secret key; please re-save the target in panel settings"
        )


def _account_prefix(target, username: str) -> str:
    """One folder per account under the destination's prefix.

    Scoping by folder is what makes a prune safe. Matching names instead meant
    a schedule for "acme" also matched user-acme2-*.tar.gz and deleted another
    account's archives.
    """
    base = (target.remote_path or "").strip().strip("/")
    return f"{base}/{username}" if base else username


def _upload_to_s3(db, target, archive: str, keep: int, username: str) -> str:
    prefix = _account_prefix(target, username)
    result = backup.upload_to_s3(
        archive,
        endpoint=target.s3_endpoint,
        region=target.s3_region,
        bucket=target.s3_bucket,
        access_key=target.s3_access_key,
        secret_key=_s3_secret(target),
        prefix=prefix,
        use_path_style=bool(target.s3_use_path_style),
    )
    # With weekday slots the folder cannot grow past seven, so this is only
    # here to clear archives written under the old timestamped scheme. It runs
    # against the account's own folder and against the legacy flat names,
    # anchored on the account prefix so a longer username is never swept up.
    if keep and keep > 0:
        for scope, name_prefix in (
            (prefix, ""),
            (target.remote_path, f"user-{username}-"),
        ):
            try:
                backup.prune_s3_backups(
                    endpoint=target.s3_endpoint,
                    region=target.s3_region,
                    bucket=target.s3_bucket,
                    access_key=target.s3_access_key,
                    secret_key=_s3_secret(target),
                    prefix=scope,
                    use_path_style=bool(target.s3_use_path_style),
                    keep=keep,
                    name_prefix=name_prefix,
                )
            except Exception:
                # The upload succeeded. A failed prune costs storage, not a
                # backup, so it must not turn a good run into a failed one --
                # which is exactly why the names rotate rather than relying on
                # this running.
                logger.warning("S3 prune failed for target %s", target.name, exc_info=True)
    return f"{target.name}:{result['remote_file']}"


def _upload_if_configured(db, schedule: BackupSchedule, archive: str, username: str = "") -> str:
    if not schedule.target_id:
        return archive
    target = db.query(BackupTarget).filter(BackupTarget.id == schedule.target_id, BackupTarget.is_active == True).first()  # noqa: E712
    if not target:
        raise ValueError("Backup target not found")

    if (target.kind or "sftp") == "s3":
        return _upload_to_s3(db, target, archive, schedule.retention, username)

    try:
        password = decrypt(target.password) if target.password else None
    except RuntimeError:
        raise RuntimeError(
            "Failed to decrypt SFTP target password; please re-save the target in panel settings"
        )
    try:
        private_key = decrypt(target.private_key) if target.private_key else None
    except RuntimeError:
        raise RuntimeError(
            "Failed to decrypt SFTP target private key; please re-save the target in panel settings"
        )
    result = backup.upload_to_sftp(
        archive,
        host=target.host,
        port=target.port,
        username=target.username,
        password=password,
        private_key=private_key,
        remote_path=target.remote_path,
        expected_host_key_type=target.host_key_type,
        expected_host_key_fingerprint=target.host_key_fingerprint,
    )
    if not target.host_key_fingerprint and result.get("host_key_fingerprint"):
        target.host_key_type = result["host_key_type"]
        target.host_key_fingerprint = result["host_key_fingerprint"]
        db.commit()
    return f"{target.name}:{result['remote_file']}"


def _decode_user_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = [item for item in raw.split(",") if item]
    if isinstance(value, int):
        value = [value]
    return [int(item) for item in value if int(item) > 0]


def _schedule_users(db, schedule: BackupSchedule) -> list[User]:
    if schedule.all_users:
        return db.query(User).filter(User.is_active == True).order_by(User.id.asc()).all()  # noqa: E712
    user_ids = _decode_user_ids(schedule.user_ids)
    if not user_ids and schedule.user_id:
        user_ids = [schedule.user_id]
    if not user_ids:
        return []
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    by_id = {user.id: user for user in users}
    return [by_id[user_id] for user_id in user_ids if user_id in by_id]


def _short_message(parts: list[str]) -> str:
    message = "; ".join(parts)
    return message[:4000]


def run_schedule(db, schedule: BackupSchedule, now: datetime | None = None,
                 on_progress=None) -> bool:
    """Run one schedule now, whether or not its cron says it is due.

    Shared by the timer and by the panel's Run now button, so a run started by
    hand is the same run -- same rotation slot, same retention, same recorded
    status -- and not a second code path that drifts from it.

    Returns True when every account succeeded.
    """
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    users = _schedule_users(db, schedule)
    if not users:
        schedule.last_run_at = now
        schedule.last_status = "error"
        schedule.last_message = "No users selected"
        db.commit()
        return False

    messages = []
    errors = []
    warnings = []
    for index, user in enumerate(users, start=1):
        if on_progress:
            on_progress(index, len(users), user.username, 0, 0, "")
        try:
            # DirectAdmin-style rotation: a week of dailies occupies seven
            # files named for the day, each overwritten a week later, so the
            # destination cannot grow without bound even if the prune below
            # never succeeds.
            slot = backup.weekday_slot(now)
            skipped: list = []
            archive = backup.create_user_backup(
                user, db, filename=f"{user.username}-{slot}.tar.gz", skipped=skipped,
                on_progress=(lambda done, total, label, _i=index:
                             on_progress(_i, len(users), user.username, done, total, label))
                if on_progress else None,
            )
            target = _upload_if_configured(db, schedule, archive, user.username)
            if schedule.target_id:
                # The week lives on the destination now; keeping it here too
                # doubled the disk a schedule with a destination was meant to
                # spare. A failed upload raised above and leaves it in place.
                backup.discard_local_copy(archive)
            backup.prune_user_backups(user.username, schedule.retention)
            messages.append(f"{user.username}: {target}")
            # An archive with a hole in it still ran, so it is not an error --
            # but the operator has to be told, or the gap only turns up when a
            # restore needs the missing file.
            if skipped:
                warnings.append(f"{user.username}: {backup.describe_skipped(skipped)}")
        except Exception as exc:  # pragma: no cover - operational path
            errors.append(f"{user.username}: {exc}")

    if errors:
        schedule.last_status = "error"
        schedule.last_message = _short_message([f"ok {len(messages)} user(s)"] + errors + warnings)
    else:
        schedule.last_status = "warning" if warnings else "ok"
        schedule.last_message = _short_message([f"ok {len(messages)} user(s)"] + warnings + messages)
    schedule.last_run_at = now
    db.commit()
    return not errors


def run_due_schedules(now: datetime | None = None) -> int:
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    db = SessionLocal()
    ran = 0
    try:
        schedules = db.query(BackupSchedule).filter(BackupSchedule.is_active == True).all()  # noqa: E712
        for schedule in schedules:
            if schedule.last_run_at and schedule.last_run_at.replace(second=0, microsecond=0) == now:
                continue
            if not _due_since(schedule.schedule, schedule.last_run_at, now):
                continue
            if run_schedule(db, schedule, now):
                ran += 1
    finally:
        db.close()
    return ran


if __name__ == "__main__":
    count = run_due_schedules()
    print(f"opanel backup scheduler ran {count} job(s).")
