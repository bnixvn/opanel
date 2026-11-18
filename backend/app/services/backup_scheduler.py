from datetime import datetime, timedelta
import json
import logging
import subprocess
import threading
from pathlib import Path

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


def _style(schedule: BackupSchedule) -> str:
    style = getattr(schedule, "name_style", None)
    return style if style in backup.NAME_STYLES else "weekday"


def _upload_to_s3(db, target, archive: str, keep: int, username: str, style: str = "weekday") -> str:
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
    # The account's own folder holds the schedule's archives: only its style's
    # names for this account count. The legacy flat names - archives written
    # under the old timestamped scheme - are anchored on the account prefix so
    # a longer username is never swept up.
    if keep and keep > 0:
        for scope, name_prefix, family in (
            (prefix, "", lambda name: backup.in_family(name, username, style)),
            (target.remote_path, f"user-{username}-", None),
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
                    family=family,
                )
            except Exception:
                # The upload succeeded. A failed prune costs storage, not a
                # backup, so it must not turn a good run into a failed one --
                # which is exactly why the names rotate rather than relying on
                # this running.
                logger.warning("S3 prune failed for target %s", target.name, exc_info=True)
    return f"{target.name}:{result['remote_file']}"


def _upload_to_addon(db, target, archive: str, keep: int, username: str, style: str) -> str:
    """An archive to a destination an addon brings (Google Drive): its own
    folder, the archive under its name - which says whose it is, as on SFTP.
    Dated and timestamped names are held to the retention, as SFTP's are."""
    from app.services import destinations

    with destinations.opened(target, db) as (driver, config):
        remote = driver.upload(config, archive, Path(archive).name)
        if style in ("date", "timestamp") and keep and keep > 0:
            try:
                ours = [row for row in driver.listing(config)
                        if backup.in_family(row["name"], username, style) and not backup.is_manual_slot(row["name"])]
                ours.sort(key=lambda row: row.get("modified") or "", reverse=True)
                for row in ours[keep:]:
                    driver.delete(config, row["key"])
            except destinations.DestinationError:
                # The upload succeeded; a failed prune costs space, not a backup.
                logger.warning("Prune failed for destination %s", target.name, exc_info=True)
    return f"{target.name}:{remote}"


def _upload_if_configured(db, schedule: BackupSchedule, archive: str, username: str = "") -> str:
    if not schedule.target_id:
        return archive
    target = db.query(BackupTarget).filter(BackupTarget.id == schedule.target_id, BackupTarget.is_active == True).first()  # noqa: E712
    if not target:
        raise ValueError("Backup target not found")

    style = _style(schedule)
    if (target.kind or "sftp") not in ("sftp", "s3"):
        return _upload_to_addon(db, target, archive, schedule.retention, username, style)
    if (target.kind or "sftp") == "s3":
        return _upload_to_s3(db, target, archive, schedule.retention, username, style)

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
    # Dated and timestamped names never repeat, so the folder is held to the
    # retention here; a weekday or a fixed name replaces itself.
    if style in ("date", "timestamp") and schedule.retention:
        try:
            backup.prune_sftp_backups(
                host=target.host, port=target.port, username=target.username,
                remote_path=target.remote_path, password=password, private_key=private_key,
                expected_host_key_type=target.host_key_type,
                expected_host_key_fingerprint=target.host_key_fingerprint,
                family=lambda name: backup.in_family(name, username, style),
                keep=schedule.retention,
            )
        except Exception:
            # The upload succeeded; a failed prune costs space, not a backup.
            logger.warning("SFTP prune failed for target %s", target.name, exc_info=True)
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


HEARTBEAT_SECONDS = 20


def _heartbeat_loop(schedule_id: int, label: list[str], stop: threading.Event) -> None:
    """Keep a running schedule's heartbeat fresh on a session of its own until
    the run ends, so the page keeps seeing it as running through a long pack or
    upload. A Session is not shared across threads, hence its own one."""
    while not stop.wait(HEARTBEAT_SECONDS):
        hb = SessionLocal()
        try:
            s = hb.get(BackupSchedule, schedule_id)
            if s is not None:
                s.running_since = datetime.utcnow()
                s.last_status = "running"
                s.last_message = label[0]
                hb.commit()
        except Exception:  # noqa: BLE001 - a heartbeat must never break the run
            logger.warning("backup schedule heartbeat failed", exc_info=True)
        finally:
            hb.close()


def run_schedule(db, schedule: BackupSchedule, now: datetime | None = None,
                 on_progress=None) -> bool:
    """Run one schedule now, whether or not its cron says it is due.

    Shared by the timer and by the panel's Run now button, so a run started by
    hand is the same run -- same rotation slot, same retention, same recorded
    status -- and not a second code path that drifts from it.

    Marks the schedule running for the whole run (a heartbeat on its own
    session), so a run started by the timer -- which has no job in the API to
    watch -- still shows as running on the page instead of looking idle until
    it finishes.

    Returns True when every account succeeded.
    """
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    users = _schedule_users(db, schedule)
    if not users:
        schedule.last_run_at = now
        schedule.last_status = "error"
        schedule.last_message = "No users selected"
        schedule.running_since = None
        db.commit()
        return False

    messages = []
    errors = []
    warnings = []
    # Running from the first moment, with a heartbeat kept fresh in the
    # background through every phase (see _heartbeat_loop).
    label = [f"Backing up {users[0].username} (1/{len(users)})"]
    schedule.running_since = datetime.utcnow()
    schedule.last_status = "running"
    schedule.last_message = label[0]
    db.commit()
    stop = threading.Event()
    # The heartbeat re-reads the row by id on a session of its own; without a
    # persisted id (a stubbed schedule in a test) there is nothing to refresh.
    schedule_id = getattr(schedule, "id", None)
    heartbeat = (threading.Thread(target=_heartbeat_loop, args=(schedule_id, label, stop), daemon=True)
                 if schedule_id is not None else None)
    if heartbeat:
        heartbeat.start()
    try:
        for index, user in enumerate(users, start=1):
            label[0] = f"Backing up {user.username} ({index}/{len(users)})"
            if on_progress:
                on_progress(index, len(users), user.username, 0, 0, "")

            def _prog(done, total, lbl, _i=index, _u=user.username, _n=len(users), _op=on_progress):
                label[0] = f"Backing up {_u} ({_i}/{_n})" + (f": {lbl}" if lbl else "")
                if _op:
                    _op(_i, _n, _u, done, total, lbl)

            try:
                # Named as the schedule says. The default is DirectAdmin-style
                # rotation: a week of dailies occupies seven files named for the
                # day, each overwritten a week later, so the destination cannot
                # grow without bound even if the prune below never succeeds.
                style = _style(schedule)
                skipped: list = []
                archive = backup.create_user_backup(
                    user, db, filename=backup.archive_filename(user.username, style, now),
                    skipped=skipped, on_progress=_prog,
                )
                label[0] = f"Uploading {user.username} ({index}/{len(users)})"
                target = _upload_if_configured(db, schedule, archive, user.username)
                if schedule.target_id:
                    # The week lives on the destination now; keeping it here too
                    # doubled the disk a schedule with a destination was meant to
                    # spare. A failed upload raised above and leaves it in place.
                    backup.discard_local_copy(archive)
                backup.prune_user_backups(user.username, schedule.retention, style)
                messages.append(f"{user.username}: {target}")
                # An archive with a hole in it still ran, so it is not an error --
                # but the operator has to be told, or the gap only turns up when a
                # restore needs the missing file.
                if skipped:
                    warnings.append(f"{user.username}: {backup.describe_skipped(skipped)}")
            except Exception as exc:  # pragma: no cover - operational path
                errors.append(f"{user.username}: {exc}")
    finally:
        stop.set()
        if heartbeat:
            heartbeat.join(timeout=5)

    if errors:
        schedule.last_status = "error"
        schedule.last_message = _short_message([f"ok {len(messages)} user(s)"] + errors + warnings)
    else:
        schedule.last_status = "warning" if warnings else "ok"
        schedule.last_message = _short_message([f"ok {len(messages)} user(s)"] + warnings + messages)
    schedule.last_run_at = now
    schedule.running_since = None
    db.commit()
    if errors or warnings:
        from app.services import notifications

        notifications.backup_schedule_failed(schedule, errors, warnings, now)
    return not errors


def _timer_run_going() -> bool:
    """The minute timer's runner is a process of its own, so a restart of the
    API leaves it writing; when it cannot be asked, assume it is."""
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", "opanel-backup-scheduler.service"],
            check=False, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def sweep_after_restart() -> list[str]:
    """What a backup this process was writing when it was stopped left behind:
    its staging folder and any ``.part`` archive. Not while the timer's runner
    is going, which may be writing one of its own."""
    if _timer_run_going():
        return []
    return backup.sweep_unfinished()


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
    # This timer is the panel's minute tick: it also sends queued notifications
    # and runs the Notifications addon's periodic checks (a no-op when the
    # addon is off).
    try:
        from app.services import notifications

        notifications.tick()
    except Exception as exc:  # noqa: BLE001 - never fail the backup run for it
        print(f"opanel notifications tick failed: {exc}")
    # WordPress Manager: each WordPress read again once a day (a no-op without the addon).
    try:
        from app.services import wp_extras

        wp_extras.daily_tick()
    except Exception as exc:  # noqa: BLE001
        print(f"opanel WordPress daily check failed: {exc}")
    # AutoSSL: a certificate for the websites whose names point here (a no-op while it is off).
    try:
        from app.services import autossl

        autossl.tick()
    except Exception as exc:  # noqa: BLE001
        print(f"opanel AutoSSL tick failed: {exc}")
    # DNS Manager: every domain on the panel has a zone (a no-op without it).
    try:
        from app.services import dns_manager

        dns_manager.tick()
    except Exception as exc:  # noqa: BLE001
        print(f"opanel DNS Manager tick failed: {exc}")
    # Resource limits: every account is in its slice, including ones made
    # outside the users page - WHMCS, a restore, a DirectAdmin import. The
    # helper only rewrites the agent's configuration when it changed.
    try:
        from app.services import resource_limits

        resource_limits.sync_quietly()
    except Exception as exc:  # noqa: BLE001
        print(f"opanel resource limits tick failed: {exc}")
    # PHP workers follow each account's sites and CPU limit and the server's
    # memory; a site whose number moved is re-rendered (one OLS restart).
    try:
        from app.services import php_workers

        changed = php_workers.reconcile_quietly()
        if changed:
            print(f"opanel PHP workers re-rendered: {', '.join(changed)}")
    except Exception as exc:  # noqa: BLE001
        print(f"opanel PHP workers tick failed: {exc}")
    # Shared blocklist: report, read the list, or take it all down once the
    # addon is off (a file read and nothing else while it never was on).
    try:
        from app.services import shared_blocklist

        shared_blocklist.tick()
    except Exception as exc:  # noqa: BLE001
        print(f"opanel shared blocklist tick failed: {exc}")
