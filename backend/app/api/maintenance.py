import json
import logging
import threading
import tarfile
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import SessionLocal, get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.core.secrets import decrypt, encrypt
from app.models.entities import BackupSchedule, BackupTarget, DatabaseAccount, User, Website
from app.schemas.schemas import (
    BackupScheduleCreate,
    BackupScheduleOut,
    BackupCreate,
    CronCreate,
    CronDelete,
    DAImportBatch,
    PhpConfigUpdate,
    PhpConfigRestore,
    RestoreBackup,
    BackupTargetCreate,
    BackupTargetOut,
    SftpBackupRun,
    UserBackupCreate,
    UserRestoreBackup,
    RemoteBackupRef,
    UserRestoreBatch,
    UserRestoreDescribe,
    WpAction,
)
from app.services import backup, backup_scheduler, cron, da_import, file_manager, mariadb, openlitespeed, php, site_users, storage_quota, wordpress
from app.services.audit import log_action

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
logger = logging.getLogger(__name__)


FILE_JOB_LIMIT = 50
_file_job_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="opanel-file-job")
_file_jobs: dict[str, dict] = {}
_file_jobs_lock = threading.Lock()

BACKUP_JOB_LIMIT = 50
_backup_job_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="opanel-backup-job")
_backup_jobs: dict[str, dict] = {}
_backup_jobs_lock = threading.Lock()


class FileWrite(BaseModel):
    website_id: int
    path: str
    content: str


class FileMkdir(BaseModel):
    website_id: int
    path: str = site_users.PUBLIC_DIR
    name: str


class FileCreate(BaseModel):
    website_id: int
    path: str = ""
    name: str


class FileRename(BaseModel):
    website_id: int
    path: str
    new_name: str


class FileChmod(BaseModel):
    website_id: int
    path: str
    mode: str


class FileBulkDelete(BaseModel):
    website_id: int
    paths: list[str]


class FileTransfer(BaseModel):
    website_id: int
    paths: list[str]
    destination_path: str = site_users.PUBLIC_DIR


class FileArchive(BaseModel):
    website_id: int
    base_path: str = site_users.PUBLIC_DIR
    paths: list[str]
    output_name: str = ""
    format: str = "zip"


class FileExtract(BaseModel):
    website_id: int
    archive_path: str
    destination_path: str = ""


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _public_file_job(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "website_id": job["website_id"],
        "archive_path": job.get("archive_path", ""),
        "destination_path": job.get("destination_path", ""),
        "target": job.get("target", ""),
        "message": job.get("message", ""),
        "error": job.get("error", ""),
        "created_at": job.get("created_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
    }


def _set_file_job(job_id: str, **updates) -> None:
    with _file_jobs_lock:
        job = _file_jobs.get(job_id)
        if not job:
            return
        job.update(updates)


def _remember_file_job(job: dict) -> dict:
    with _file_jobs_lock:
        _file_jobs[job["job_id"]] = job
        if len(_file_jobs) > FILE_JOB_LIMIT:
            # Find oldest completed jobs to remove (O(n) instead of O(n log n))
            to_remove = len(_file_jobs) - FILE_JOB_LIMIT
            removable = [
                (job.get("created_at", ""), job_id)
                for job_id, job in _file_jobs.items()
                if job.get("status") not in {"queued", "running"}
            ]
            # Sort only the removable jobs, take the oldest to_remove items
            removable.sort(key=lambda x: x[0])
            for _, job_id in removable[:to_remove]:
                _file_jobs.pop(job_id, None)
    return _public_file_job(job)


def _get_file_job(job_id: str) -> dict | None:
    with _file_jobs_lock:
        job = _file_jobs.get(job_id)
        return dict(job) if job else None


def _list_file_jobs(current_user: User, website_id: int | None = None) -> list[dict]:
    with _file_jobs_lock:
        jobs = [dict(job) for job in _file_jobs.values()]
    visible = []
    for job in jobs:
        if job.get("user_id") != current_user.id and not is_admin_role(current_user.role):
            continue
        if website_id is not None and job.get("website_id") != website_id:
            continue
        visible.append(_public_file_job(job))
    return sorted(visible, key=lambda item: item.get("created_at", ""), reverse=True)[:10]


def _public_backup_job(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "website_id": job.get("website_id"),
        "user_id": job.get("target_user_id"),
        "target_id": job.get("target_id"),
        "backup_file": job.get("backup_file", ""),
        "remote_file": job.get("remote_file", ""),
        "target": job.get("target", ""),
        "message": job.get("message", ""),
        "error": job.get("error", ""),
        "created_at": job.get("created_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
        # None when the current step has nothing countable in it; the bar
        # sweeps rather than standing at a figure nobody computed.
        # Which schedule this run belongs to, so the schedule's own row
        # can show it. Pressing Run now and then having to find the
        # progress on another tab is how it reads as doing nothing.
        "schedule_id": job.get("schedule_id"),
        "progress_percent": job.get("progress_percent"),
        "progress_label": job.get("progress_label", ""),
    }


def _nested_percent(outer_done: float, outer_total: int,
                    inner_done: int = 0, inner_total: int = 0) -> float | None:
    """Where a run is, counting the units it actually has.

    Outer is whole items finished (accounts, archives); inner is how far into
    the current one. Nothing is inferred from elapsed time -- a 5 GB site and a
    5 MB one take wildly different amounts of it.
    """
    if not outer_total:
        return None
    share = 1.0 / outer_total
    # outer_done may be fractional: "two sites finished and 30% through the
    # third" arrives as 2.3, so the bar moves inside a site as well as between.
    done = max(0.0, outer_done - 1) * share
    if inner_total:
        done += share * min(1.0, inner_done / inner_total)
    return round(min(100.0, max(0.0, done * 100)), 1)


def _set_backup_job(job_id: str, **updates) -> None:
    with _backup_jobs_lock:
        job = _backup_jobs.get(job_id)
        if not job:
            return
        job.update(updates)


def _remember_backup_job(job: dict) -> dict:
    with _backup_jobs_lock:
        _backup_jobs[job["job_id"]] = job
        if len(_backup_jobs) > BACKUP_JOB_LIMIT:
            to_remove = len(_backup_jobs) - BACKUP_JOB_LIMIT
            removable = [
                (job.get("created_at", ""), job_id)
                for job_id, job in _backup_jobs.items()
                if job.get("status") not in {"queued", "running"}
            ]
            removable.sort(key=lambda x: x[0])
            for _, job_id in removable[:to_remove]:
                _backup_jobs.pop(job_id, None)
    return _public_backup_job(job)


def _get_backup_job(job_id: str) -> dict | None:
    with _backup_jobs_lock:
        job = _backup_jobs.get(job_id)
        return dict(job) if job else None


def _list_backup_jobs(current_user: User) -> list[dict]:
    with _backup_jobs_lock:
        jobs = [dict(job) for job in _backup_jobs.values()]
    visible = []
    for job in jobs:
        if job.get("request_user_id") != current_user.id and not is_admin_role(current_user.role):
            continue
        visible.append(_public_backup_job(job))
    return sorted(visible, key=lambda item: item.get("created_at", ""), reverse=True)[:12]


def _queue_backup_job(current_user: User, kind: str, message: str, **extra) -> dict:
    job = {
        "job_id": uuid.uuid4().hex,
        "kind": kind,
        "status": "queued",
        "request_user_id": current_user.id,
        "message": message,
        "error": "",
        "backup_file": "",
        "remote_file": "",
        "target": "",
        "created_at": _now_iso(),
        "started_at": "",
        "finished_at": "",
        # None, not 0: a job whose work cannot be counted -- taring one
        # site's tree -- says so, and the bar reads "working" instead of
        # sitting at a number nobody computed.
        "progress_percent": None,
        "progress_label": "",
        **extra,
    }
    return _remember_backup_job(job)



def _run_extract_job(job_id: str, user_id: int, website_id: int, archive_path: str, destination_path: str, allow_executable: bool) -> None:
    _set_file_job(job_id, status="running", started_at=_now_iso(), message="Extracting archive")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        website = db.query(Website).filter(Website.id == website_id).first()
        if not user or not user.is_active:
            raise ValueError("User not found")
        if not website:
            raise ValueError("Website not found")
        if website.owner_id != user.id and not is_admin_role(user.role):
            raise ValueError("Access denied")
        target = file_manager.extract_archive(
            website,
            archive_path,
            destination_path,
            allow_executable,
            quota_check=_quota_check_for_website(db, website),
            quota_headroom=storage_quota.user_storage_headroom_bytes(db, website.owner),
        )
        log_action(db, user.id, "extract_archive", website.domain, archive_path)
        _set_file_job(
            job_id,
            status="done",
            target=target,
            message="Extraction completed",
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("File extract job failed: job_id=%s website_id=%s", job_id, website_id)
        _set_file_job(
            job_id,
            status="error",
            error=str(exc),
            message="Extraction failed",
            finished_at=_now_iso(),
        )
    finally:
        db.close()


def _queue_extract_job(user: User, website: Website, archive_path: str, destination_path: str, allow_executable: bool) -> dict:
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "kind": "extract_archive",
        "status": "queued",
        "user_id": user.id,
        "website_id": website.id,
        "archive_path": archive_path,
        "destination_path": destination_path,
        "target": "",
        "message": "Extraction queued",
        "error": "",
        "created_at": _now_iso(),
        "started_at": "",
        "finished_at": "",
    }
    public_job = _remember_file_job(job)
    _file_job_executor.submit(
        _run_extract_job,
        job_id,
        user.id,
        website.id,
        archive_path,
        destination_path,
        allow_executable,
    )
    return public_job


def get_owned_website(db: Session, current_user: User, website_id: int) -> Website:
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    if website.owner_id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    return website


def get_backup_user(db: Session, current_user: User, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    return user


def _decrypted(value, label: str):
    """A secret that will not decrypt means the key changed under us. Say that,
    rather than letting a NULL reach the transport as an empty password."""
    if not value:
        return None
    try:
        return decrypt(value)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not decrypt the {label} for this target. Re-save it in panel settings.",
        ) from exc


def _target_folder(target, folder: str) -> str:
    """One folder per account or site under the destination's prefix.

    A backup sent by hand used to land flat at the base prefix while the
    scheduler wrote into <prefix>/<account>/. The two never met, so the
    schedule's retention could not see what the manual runs left behind.
    """
    base = (target.remote_path or "").strip().strip("/")
    safe = (folder or "").strip().strip("/")
    if not safe:
        return base
    return f"{base}/{safe}" if base else safe


def _upload_to_s3_target(target, archive: str, folder: str = "") -> tuple[str, str]:
    result = backup.upload_to_s3(
        archive,
        endpoint=target.s3_endpoint,
        region=target.s3_region,
        bucket=target.s3_bucket,
        access_key=target.s3_access_key,
        secret_key=_decrypted(target.s3_secret_key, "S3 secret key"),
        prefix=_target_folder(target, folder),
        use_path_style=bool(target.s3_use_path_style),
    )
    return target.name, result["remote_file"]


def upload_archive_to_target(db: Session, target_id: int, archive: str,
                             folder: str = "") -> tuple[str, str]:
    target = db.query(BackupTarget).filter(BackupTarget.id == target_id).first()
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Backup target not found")

    if (target.kind or "sftp") == "s3":
        try:
            return _upload_to_s3_target(target, archive, folder)
        except backup.S3Error as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        result = backup.upload_to_sftp(
            archive,
            host=target.host,
            port=target.port,
            username=target.username,
            password=_decrypted(target.password, "SFTP password"),
            private_key=_decrypted(target.private_key, "SFTP private key"),
            remote_path=target.remote_path,
            expected_host_key_type=target.host_key_type,
            expected_host_key_fingerprint=target.host_key_fingerprint,
        )
    except backup.SftpHostKeyMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not target.host_key_fingerprint and result.get("host_key_fingerprint"):
        target.host_key_type = result["host_key_type"]
        target.host_key_fingerprint = result["host_key_fingerprint"]
        db.commit()
    return target.name, result["remote_file"]


def _run_site_backup_job(job_id: str, request_user_id: int, website_id: int) -> None:
    _set_backup_job(job_id, status="running", started_at=_now_iso(), message="Creating website backup")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == request_user_id).first()
        if not user or not user.is_active:
            raise ValueError("User not found")
        website = get_owned_website(db, user, website_id)
        db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
        skipped: list = []
        archive = backup.create_backup(website, db_item.db_name if db_item else None, skipped=skipped)
        log_action(db, user.id, "backup", website.domain, archive)
        note = backup.describe_skipped(skipped)
        _set_backup_job(
            job_id,
            status="done",
            backup_file=archive,
            message="Website backup completed" + (f". {note}" if note else ""),
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Website backup job failed: job_id=%s website_id=%s", job_id, website_id)
        _set_backup_job(job_id, status="error", error=str(exc), message="Website backup failed", finished_at=_now_iso())
    finally:
        db.close()


def _run_user_backup_job(job_id: str, request_user_id: int, target_user_id: int, target_id: int | None) -> None:
    _set_backup_job(job_id, status="running", started_at=_now_iso(), message="Creating full user backup")
    db = SessionLocal()
    try:
        request_user = db.query(User).filter(User.id == request_user_id).first()
        if not request_user or not request_user.is_active:
            raise ValueError("User not found")
        user = get_backup_user(db, request_user, target_user_id)
        skipped: list = []

        def progress(done, total, label):
            _set_backup_job(job_id, progress_percent=_nested_percent(1, 1, done, total),
                            progress_label=label,
                            message=f"Archiving {label}" if label else "Finishing archive")

        archive = backup.create_user_backup(user, db, skipped=skipped, on_progress=progress)
        remote_file = ""
        target_name = ""
        if target_id:
            ensure_role(request_user.role, Role.admin)
            target_name, remote_file = upload_archive_to_target(db, target_id, archive,
                                                                folder=user.username)
        detail = f"{archive}" + (f" -> {target_name}:{remote_file}" if remote_file else "")
        log_action(db, request_user.id, "backup_user", user.username, detail)
        if remote_file:
            # Sent off-server, so not kept here too; see discard_local_copy.
            backup.discard_local_copy(archive)
        note = backup.describe_skipped(skipped)
        _set_backup_job(
            job_id,
            status="done",
            backup_file="" if remote_file else archive,
            remote_file=remote_file,
            target=target_name,
            message="Full user backup completed" + (f". {note}" if note else ""),
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Full user backup job failed: job_id=%s user_id=%s", job_id, target_user_id)
        _set_backup_job(job_id, status="error", error=str(exc), message="Full user backup failed", finished_at=_now_iso())
    finally:
        db.close()


def _run_sftp_backup_job(job_id: str, request_user_id: int, website_id: int, target_id: int) -> None:
    _set_backup_job(job_id, status="running", started_at=_now_iso(), message="Creating and uploading SFTP backup")
    db = SessionLocal()
    try:
        request_user = db.query(User).filter(User.id == request_user_id).first()
        if not request_user or not request_user.is_active:
            raise ValueError("User not found")
        ensure_role(request_user.role, Role.admin)
        website = get_owned_website(db, request_user, website_id)
        db_item = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
        skipped: list = []
        archive = backup.create_backup(website, db_item.db_name if db_item else None, skipped=skipped)
        target_name, remote_file = upload_archive_to_target(db, target_id, archive,
                                                            folder=website.domain)
        log_action(db, request_user.id, "backup_sftp", website.domain, f"{target_name}:{remote_file}")
        backup.discard_local_copy(archive)
        note = backup.describe_skipped(skipped)
        _set_backup_job(
            job_id,
            status="done",
            backup_file="",
            remote_file=remote_file,
            target=target_name,
            message="SFTP backup completed" + (f". {note}" if note else ""),
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("SFTP backup job failed: job_id=%s website_id=%s", job_id, website_id)
        _set_backup_job(job_id, status="error", error=str(exc), message="SFTP backup failed", finished_at=_now_iso())
    finally:
        db.close()


def _queue_site_backup(current_user: User, website: Website) -> dict:
    job = _queue_backup_job(current_user, "site_backup", "Website backup queued", website_id=website.id)
    _backup_job_executor.submit(_run_site_backup_job, job["job_id"], current_user.id, website.id)
    return job


def _queue_user_backup(current_user: User, user: User, target_id: int | None) -> dict:
    job = _queue_backup_job(
        current_user,
        "user_backup",
        "Full user backup queued",
        target_user_id=user.id,
        target_id=target_id,
    )
    _backup_job_executor.submit(_run_user_backup_job, job["job_id"], current_user.id, user.id, target_id)
    return job


def _run_schedule_now_job(job_id: str, request_user_id: int, schedule_id: int) -> None:
    _set_backup_job(job_id, status="running", started_at=_now_iso(), message="Running schedule")
    db = SessionLocal()
    try:
        schedule = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
        if not schedule:
            raise ValueError("Backup schedule not found")

        def progress(index, total, username, done=0, of=0, label="") -> None:
            where = f" - {label}" if label else ""
            _set_backup_job(
                job_id,
                progress_percent=_nested_percent(index, total, done, of),
                progress_label=f"{username}{where}",
                message=f"Backing up {username} ({index}/{total}){where}",
            )

        ok = backup_scheduler.run_schedule(db, schedule, on_progress=progress)
        db.refresh(schedule)
        _set_backup_job(
            job_id,
            progress_percent=100.0,
            progress_label="",
            # A run that skipped an unreadable file still ran; the schedule's
            # own status carries that distinction, so pass it through rather
            # than flattening it to done/error.
            status="done" if ok else "error",
            message=schedule.last_message or ("Schedule finished" if ok else "Schedule failed"),
            error="" if ok else (schedule.last_message or "Schedule failed"),
            finished_at=_now_iso(),
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Run-now backup schedule failed: job_id=%s schedule_id=%s", job_id, schedule_id)
        _set_backup_job(job_id, status="error", error=str(exc),
                        message="Schedule failed", finished_at=_now_iso())
    finally:
        db.close()


def _queue_sftp_backup(current_user: User, website: Website, target_id: int) -> dict:
    job = _queue_backup_job(
        current_user,
        "sftp_backup",
        "SFTP backup queued",
        website_id=website.id,
        target_id=target_id,
    )
    _backup_job_executor.submit(_run_sftp_backup_job, job["job_id"], current_user.id, website.id, target_id)
    return job


def _save_user_restore_upload(file: UploadFile) -> dict:
    target = ""
    try:
        target = backup.save_uploaded_user_backup(file.filename or "user-backup.tar.gz", file.file)
        manifest = backup.read_backup_manifest(target)
        if manifest.get("kind") not in backup.RESTORABLE_BACKUP_KINDS:
            raise ValueError("This is not a full user backup")
    except (ValueError, FileNotFoundError) as exc:
        if target:
            try:
                backup.delete_user_backup(target)
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = backup.user_backup_path(target)
    return {
        "backup_file": target,
        "filename": path.name,
        "username": (manifest.get("user") or {}).get("username"),
        "generated_at": manifest.get("generated_at"),
        "websites": len(manifest.get("websites") or []),
        "size": path.stat().st_size,
        "valid": True,
        "error": "",
    }


def _quota_check_for_website(db: Session, website: Website):
    owner = website.owner

    def check(incoming_bytes: int, replaced_bytes: int = 0) -> None:
        storage_quota.enforce_user_storage_quota(
            db,
            owner,
            incoming_bytes=incoming_bytes,
            replaced_bytes=replaced_bytes,
        )

    return check


@router.post("/backup")
def create_backup(payload: BackupCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    return _queue_site_backup(current_user, website)


@router.get("/backup-jobs")
def list_backup_jobs(current_user: User = Depends(get_current_user)):
    return {"jobs": _list_backup_jobs(current_user)}


@router.get("/backup-jobs/{job_id}")
def get_backup_job(job_id: str, current_user: User = Depends(get_current_user)):
    job = _get_backup_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Backup job not found")
    if job.get("request_user_id") != current_user.id and not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="Access denied")
    return _public_backup_job(job)


@router.post("/restore")
def restore_backup(payload: RestoreBackup, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        path = backup.restore_backup(website, payload.backup_file)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if website.linux_user:
        runtime_php_version = website.php_version if (website.app_type or "wordpress") in {"wordpress", "php"} else None
        site_users.ensure_site_runtime(website.domain, website.root_path, runtime_php_version, website.linux_user)
    wordpress.fix_permissions(website.root_path, website.linux_user)
    database_restored = False
    database_note = ""
    account = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
    if payload.restore_database:
        if not account:
            database_note = "No database is linked to this website, so only files were restored."
        else:
            try:
                database_restored = backup.restore_backup_database(
                    website,
                    payload.backup_file,
                    account.db_name,
                    db_user=account.db_user,
                    db_password=decrypt(account.db_password),
                )
                if not database_restored:
                    database_note = "This backup contains no SQL dump, so only files were restored."
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"Files restored, but the database import failed: {exc}"
                ) from exc
    elif backup.archive_has_database(website, payload.backup_file):
        database_note = ("Files restored. This backup also holds a database dump, which was "
                         "left alone -- re-run with restore_database to import it.")
    log_action(db, current_user.id, "restore", website.domain, payload.backup_file)
    return {"restored_to": path, "database_restored": database_restored, "note": database_note}


@router.get("/backups/{website_id}")
def list_backups(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    return {"items": backup.list_backups(website.domain)}


@router.get("/backups/{website_id}/download")
def download_backup(website_id: int, backup_file: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        path = backup.backup_path(website.domain, backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    return FileResponse(str(path), filename=path.name, media_type="application/gzip")


@router.delete("/backups/{website_id}")
def delete_backup(
    website_id: int,
    backup_file: str,
    # Delete removes both copies. A backup rotating daily against a fixed
    # retention leaves a copy the panel cannot reach, and the bucket fills.
    also_remote: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    website = get_owned_website(db, current_user, website_id)
    relative_key = _remote_relative_key(backup_file)
    try:
        deleted = backup.delete_backup(website.domain, backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found")
    removed_remote = _delete_remote_copies(db, relative_key) if also_remote else []
    log_action(db, current_user.id, "delete_backup", website.domain, deleted)
    return {"deleted": deleted, "removed_remote": removed_remote}


@router.post("/backups/{website_id}/upload")
def upload_backup(website_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        target = backup.save_uploaded_backup(website.domain, file.filename or "backup.tar.gz", file.file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_backup", website.domain, target)
    return {"backup_file": target}


@router.get("/user-restore-backups")
def list_user_restore_backups(current_user: User = Depends(get_current_user)):
    """Everything that can be restored, wherever it happens to live.

    One list: archives uploaded for restore, and the ones the panel made for
    each account. The operator should not have to know which folder holds
    which.
    """
    ensure_role(current_user.role, Role.admin)
    return {"directory": backup.user_restore_dir(), "items": backup.list_all_restorable_backups()}


@router.post("/user-restore-backups/describe")
def describe_restore_backups(payload: UserRestoreDescribe, current_user: User = Depends(get_current_user)):
    """What is inside these archives: owner, site count, whether it is valid.

    Separate from the listing because finding a manifest in an archive written
    before the manifest moved to the front means decompressing all of it --
    159 seconds for the seventeen on the production box. The list paints first;
    this fills it in.
    """
    ensure_role(current_user.role, Role.admin)
    return {"items": backup.describe_backups(payload.backup_files)}


@router.get("/user-restore-remote")
def list_remote_restore_backups(db: Session = Depends(get_db),
                                current_user: User = Depends(get_current_user)):
    """What is sitting on each active S3 destination.

    Listing only: an object cannot be described without pulling it down, and
    nothing is pulled down until someone asks for it. Reported per target so a
    failing destination does not hide the ones that answered.
    """
    ensure_role(current_user.role, Role.admin)
    targets = db.query(BackupTarget).filter(
        BackupTarget.is_active == True, BackupTarget.kind == "s3"  # noqa: E712
    ).all()
    items, errors = [], []
    for target in targets:
        try:
            rows = backup.list_s3_backups(
                endpoint=target.s3_endpoint,
                region=target.s3_region,
                bucket=target.s3_bucket,
                access_key=target.s3_access_key,
                secret_key=_decrypted(target.s3_secret_key, "S3 secret key"),
                prefix=target.remote_path,
                use_path_style=bool(target.s3_use_path_style),
            )
        except Exception as exc:
            errors.append(f"{target.name}: {exc}")
            continue
        base = backup.s3_prefix_for(target.remote_path)
        for row in rows:
            key = row["key"]
            tail = key[len(base):].lstrip("/") if base else key
            # <account>/<file> is how both the scheduler and a manual upload
            # write; anything flatter is from before that and has no folder to
            # take the account from.
            account = tail.split("/")[0] if "/" in tail else ""
            items.append({
                "target_id": target.id,
                "target": target.name,
                "bucket": target.s3_bucket,
                "key": key,
                "filename": key.rsplit("/", 1)[-1],
                "account": account,
                "size": row.get("size") or 0,
                "modified_at": str(row.get("modified") or ""),
                "source": "s3",
            })
    items.sort(key=lambda row: (row["target"], row["account"], row["modified_at"]), reverse=True)
    return {"items": items, "errors": errors}


def _fetch_remote_archive(db, job_id: str, target_id: int, key: str, position: str) -> str:
    """Bring one object down so it can be restored.

    The operator picked a backup, not a download: which side of the network
    it was on is the panel's problem, not a step to hand them.
    """
    target = db.query(BackupTarget).filter(
        BackupTarget.id == target_id,
        BackupTarget.is_active == True,  # noqa: E712
        BackupTarget.kind == "s3",
    ).first()
    if not target:
        raise ValueError("Backup target not found")
    _set_backup_job(job_id, message=f"Downloading {key.rsplit('/', 1)[-1]} {position}")
    return backup.download_from_s3(
        key,
        endpoint=target.s3_endpoint,
        region=target.s3_region,
        bucket=target.s3_bucket,
        access_key=target.s3_access_key,
        secret_key=_decrypted(target.s3_secret_key, "S3 secret key"),
        use_path_style=bool(target.s3_use_path_style),
        destination_dir=backup.user_restore_dir(),
    )


def _run_restore_batch_job(job_id: str, request_user_id: int, items: list[dict]) -> None:
    _set_backup_job(job_id, status="running", started_at=_now_iso(),
                    message=f"Restoring 1 of {len(items)}")
    db = SessionLocal()
    done, failures = [], []
    try:
        for index, item in enumerate(items, start=1):
            position = f"({index} of {len(items)})"
            backup_file = item.get("backup_file") or ""
            fetched = ""
            name = (backup_file or item.get("key", "")).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            try:
                if not backup_file:
                    backup_file = _fetch_remote_archive(
                        db, job_id, item["target_id"], item["key"], position)
                    fetched = backup_file
            except Exception as exc:
                logger.exception("Could not fetch %s", item.get("key"))
                failures.append(f"{name}: {exc}")
                continue
            _set_backup_job(job_id, message=f"Restoring {name} {position}",
                            progress_percent=_nested_percent(index, len(items), 0, 0),
                            progress_label=name)

            def site_progress(done, of, label, _i=index, _n=name):
                _set_backup_job(
                    job_id,
                    progress_percent=_nested_percent(_i, len(items), done, of),
                    progress_label=f"{_n} - {label}" if label else _n,
                    message=f"Restoring {_n} {position}" + (f" - {label}" if label else ""),
                )

            try:
                result = backup.restore_user_backup(backup_file, db, on_progress=site_progress)
            except Exception as exc:
                # One archive failing must not abandon the rest: they are
                # separate accounts, and the operator picked them all.
                db.rollback()
                logger.exception("Restore failed for %s", backup_file)
                failures.append(f"{name}: {exc}")
                continue
            finally:
                # A copy pulled down only to be restored is not a local
                # backup: the destination still holds it, and a 5 GB archive
                # left in the restore folder is disk nobody asked for.
                if fetched:
                    Path(fetched).unlink(missing_ok=True)
            log_action(db, request_user_id, "restore_user", result.get("username", "user"), backup_file)
            note = f"{result.get('username', name)}: {len(result.get('websites') or [])} site(s)"
            certs = result.get("certificates") or []
            if certs:
                note += f", {len(certs)} cert(s)"
            if result.get("ssl_warnings"):
                note += f", SSL not restored for {len(result['ssl_warnings'])}"
            done.append(note)
        summary = "; ".join(done + failures)[:4000]
        _set_backup_job(
            job_id,
            progress_percent=100.0,
            progress_label="",
            status="error" if failures else "done",
            message=summary or "Nothing restored",
            error="; ".join(failures)[:2000] if failures else "",
            finished_at=_now_iso(),
        )
    finally:
        db.close()


@router.post("/user-restore-batch")
def restore_user_backups(payload: UserRestoreBatch, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """Restore several archives in one run.

    Sequential on purpose. Each restore rewrites site files, imports databases
    and reloads OpenLiteSpeed; running them at once would have them fighting
    over the same web server.
    """
    ensure_role(current_user.role, Role.admin)
    files = [item for item in (payload.backup_files or []) if item.strip()]
    for backup_file in files:
        try:
            backup.user_backup_path(backup_file)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"{backup_file}: {exc}") from exc
    remote = payload.remote_items or []
    for ref in remote:
        known = db.query(BackupTarget).filter(
            BackupTarget.id == ref.target_id,
            BackupTarget.is_active == True,  # noqa: E712
            BackupTarget.kind == "s3",
        ).first()
        if not known:
            raise HTTPException(status_code=404, detail=f"Backup target {ref.target_id} not found")

    items = ([{"backup_file": path} for path in files]
             + [{"target_id": ref.target_id, "key": ref.key} for ref in remote])
    job = _queue_backup_job(current_user, "user_restore_batch",
                            f"Restore of {len(items)} backup(s) queued", count=len(items))
    _backup_job_executor.submit(_run_restore_batch_job, job["job_id"], current_user.id, items)
    names = ([path.rsplit("/", 1)[-1] for path in files]
             + [ref.key.rsplit("/", 1)[-1] for ref in remote])
    log_action(db, current_user.id, "restore_user_batch", f"{len(items)} backup(s)",
               ", ".join(names)[:500], request=request)
    return job


@router.post("/user-restore-backups/upload")
def upload_user_restore_backups(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    if not files:
        raise HTTPException(status_code=400, detail="No backup files uploaded")
    items = [_save_user_restore_upload(file) for file in files]
    users = ", ".join(item.get("username") or item.get("filename") or "user" for item in items)
    log_action(db, current_user.id, "upload_user_restore_backups", "restore_folder", users)
    return {"directory": backup.user_restore_dir(), "items": items}


@router.delete("/user-restore-backups")
def delete_user_restore_backup(backup_file: str, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        deleted = backup.delete_user_restore_backup(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    log_action(db, current_user.id, "delete_user_restore_backup", "restore_folder", deleted, request=request)
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# DirectAdmin backup import
# ---------------------------------------------------------------------------

# Enough to keep every result of the largest batch on screen: a finished job is
# evicted to make room, so a limit below the batch size would drop the first
# archives' outcomes before the last ones had run.
DA_IMPORT_JOB_LIMIT = 200
_da_import_job_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="opanel-da-import")
_da_import_jobs: dict[str, dict] = {}
_da_import_jobs_lock = threading.Lock()


def _public_da_import_job(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "backup_file": job.get("backup_file", ""),
        "overwrite": bool(job.get("overwrite")),
        "message": job.get("message", ""),
        "error": job.get("error", ""),
        "summary": job.get("summary"),
        "created_at": job.get("created_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
    }


def _set_da_import_job(job_id: str, **updates) -> None:
    with _da_import_jobs_lock:
        job = _da_import_jobs.get(job_id)
        if not job:
            return
        job.update(updates)


def _remember_da_import_job(job: dict) -> dict:
    with _da_import_jobs_lock:
        _da_import_jobs[job["job_id"]] = job
        if len(_da_import_jobs) > DA_IMPORT_JOB_LIMIT:
            removable = [
                (j.get("created_at", ""), jid)
                for jid, j in _da_import_jobs.items()
                if j.get("status") not in {"queued", "running"}
            ]
            removable.sort(key=lambda x: x[0])
            for _, jid in removable[: len(_da_import_jobs) - DA_IMPORT_JOB_LIMIT]:
                _da_import_jobs.pop(jid, None)
    return _public_da_import_job(job)


def _run_da_import_job(job_id: str, backup_file: str, overwrite: bool = False) -> None:
    _set_da_import_job(job_id, status="running", started_at=datetime.utcnow().isoformat() + "Z",
                       message="Starting DirectAdmin import...")
    db = SessionLocal()
    try:
        summary = da_import.import_da_backup(backup_file, db, overwrite=overwrite)
        imported = len(summary.get("imported_domains", []))
        subs = len(summary.get("subdomains", []))
        message = f"Imported {imported} domain(s)"
        if subs:
            message += f" (incl. {subs} DirectAdmin subdomain(s))"
        _set_da_import_job(
            job_id,
            status="done",
            summary=summary,
            message=message,
            finished_at=datetime.utcnow().isoformat() + "Z",
        )
    except Exception as exc:
        db.rollback()
        logger.exception("DA import job failed: job_id=%s file=%s", job_id, backup_file)
        _set_da_import_job(
            job_id,
            status="error",
            error=str(exc),
            message="Import failed",
            finished_at=datetime.utcnow().isoformat() + "Z",
        )
    finally:
        db.close()


@router.get("/da-backups")
def list_da_backups(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"directory": da_import.da_backup_dir(), "items": da_import.list_da_backups()}


@router.post("/da-backups/upload")
def upload_da_backups(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    if not files:
        raise HTTPException(status_code=400, detail="No backup files uploaded")
    items = []
    for file in files:
        try:
            item = da_import.save_da_backup(file.filename or "backup.tar.gz", file.file)
            items.append(item)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_da_backups", "da_backup_dir",
               ", ".join(item["filename"] for item in items))
    return {"directory": da_import.da_backup_dir(), "items": items}


@router.delete("/da-backups")
def delete_da_backup(backup_file: str, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        deleted = da_import.delete_da_backup(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    log_action(db, current_user.id, "delete_da_backup", "da_backup_dir", deleted, request=request)
    return {"deleted": deleted}


def _queue_da_import(backup_file: str, overwrite: bool) -> dict:
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "queued",
        "backup_file": backup_file,
        "overwrite": overwrite,
        "message": "Queued",
        "error": "",
        "summary": None,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "started_at": "",
        "finished_at": "",
    }
    public = _remember_da_import_job(job)
    _da_import_job_executor.submit(_run_da_import_job, job_id, backup_file, overwrite)
    return public


@router.post("/da-import")
def start_da_import(backup_file: str, request: Request, db: Session = Depends(get_db),
                    overwrite: bool = False,
                    current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        da_import.da_backup_path(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"{backup_file}: {exc}") from exc
    job = _queue_da_import(backup_file, overwrite)
    log_action(db, current_user.id, "start_da_import", backup_file,
               "overwrite" if overwrite else "", request=request)
    return job


@router.post("/da-import-batch")
def start_da_import_batch(payload: DAImportBatch, request: Request, db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    """Queue several DirectAdmin archives at once.

    One job per archive on the single-worker executor, so they still run one
    after another -- each import creates Linux users, writes vhosts and reloads
    OpenLiteSpeed, and two at once would race on all three. What this saves is
    the operator's time: tick them all, start once, come back to the results.
    """
    ensure_role(current_user.role, Role.admin)
    files = list(dict.fromkeys(item.strip() for item in payload.backup_files if item.strip()))
    if not files:
        raise HTTPException(status_code=400, detail="Select at least one backup")
    # Check every name before queueing any, so a typo does not leave half a
    # batch running.
    for backup_file in files:
        try:
            da_import.da_backup_path(backup_file)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"{backup_file}: {exc}") from exc
    # A second click while the first batch is still going would import the same
    # archive twice: the repeat either fails on the clash or, with overwrite,
    # redoes the whole import for nothing.
    with _da_import_jobs_lock:
        pending = {
            j.get("backup_file") for j in _da_import_jobs.values()
            if j.get("status") in {"queued", "running"}
        }
    skipped = [f for f in files if f in pending]
    jobs = [_queue_da_import(f, payload.overwrite) for f in files if f not in pending]
    if jobs:
        log_action(db, current_user.id, "start_da_import_batch",
                   f"{len(jobs)} archive(s)" + (" overwrite" if payload.overwrite else ""),
                   ", ".join(j["backup_file"] for j in jobs)[:500], request=request)
    return {"jobs": jobs, "skipped": skipped}


@router.get("/da-import/jobs")
def list_da_import_jobs(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    with _da_import_jobs_lock:
        jobs = [_public_da_import_job(dict(j)) for j in _da_import_jobs.values()]
    return {"jobs": sorted(jobs, key=lambda j: j.get("created_at", ""), reverse=True)}


@router.get("/da-import/jobs/{job_id}")
def get_da_import_job(job_id: str, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    with _da_import_jobs_lock:
        job = _da_import_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _public_da_import_job(dict(job))


@router.get("/user-backups/{user_id}")
def list_user_backups(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user = get_backup_user(db, current_user, user_id)
    items = backup.list_user_backups(user.username)
    if is_admin_role(current_user.role):
        items.extend(item for item in backup.list_uploaded_user_backups(user.username) if item not in items)
    return {"items": items}


@router.post("/user-backup")
def create_user_backup(payload: UserBackupCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    user = get_backup_user(db, current_user, payload.user_id)
    if payload.target_id:
        ensure_role(current_user.role, Role.admin)
        target_exists = db.query(BackupTarget).filter(BackupTarget.id == payload.target_id, BackupTarget.is_active == True).first()  # noqa: E712
        if not target_exists:
            raise HTTPException(status_code=404, detail="Backup target not found")
    log_action(db, current_user.id, "queue_backup_user", user.username, request=request)
    return _queue_user_backup(current_user, user, payload.target_id)


@router.get("/user-backups-download")
def download_user_backup(backup_file: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        path = backup.user_backup_path(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    return FileResponse(str(path), filename=path.name, media_type="application/gzip")


@router.post("/user-backups/upload")
def upload_user_backup(file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    item = _save_user_restore_upload(file)
    log_action(db, current_user.id, "upload_user_backup", item.get("username") or "user", item["backup_file"])
    return {"backup_file": item["backup_file"], "username": item.get("username")}


@router.post("/user-restore")
def restore_user_backup(payload: UserRestoreBackup, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        result = backup.restore_user_backup(payload.backup_file, db)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "restore_user", result.get("username", "user"), payload.backup_file, request=request)
    return result


def _remote_relative_key(local_path: str) -> str:
    """The key a local backup occupies under a destination's prefix.

    A full user backup lives at <backup_root>/users/<account>/<file>, and it is
    uploaded to <prefix>/<account>/<file>. The account has to stay in the key:
    weekday rotation names every account's Monday copy "monday.tar.gz", so a
    filename on its own identifies one object per account rather than one
    object.
    """
    parts = Path(local_path).parts
    if "users" in parts:
        index = len(parts) - 1 - parts[::-1].index("users")
        tail = parts[index + 1:]
        if len(tail) >= 2:
            return "/".join(tail[-2:])
    return parts[-1] if parts else local_path


def _remote_copies(db: Session, relative_key: str) -> list[dict]:
    """Every S3 destination holding the object at this relative key.

    A backup is not recorded against the destination it was sent to, so its
    position under each active destination's prefix is what identifies it.
    Listing first means the caller can be told exactly what will go.
    """
    found = []
    targets = db.query(BackupTarget).filter(
        BackupTarget.kind == "s3", BackupTarget.is_active == True  # noqa: E712
    ).all()
    for target in targets:
        try:
            rows = backup.list_s3_backups(
                endpoint=target.s3_endpoint,
                region=target.s3_region,
                bucket=target.s3_bucket,
                access_key=target.s3_access_key,
                secret_key=_decrypted(target.s3_secret_key, "S3 secret key"),
                prefix=target.remote_path,
                use_path_style=bool(target.s3_use_path_style),
            )
        except Exception:
            # A destination that cannot be reached must not block deleting the
            # local file; say nothing about it rather than failing the request.
            logger.warning("Could not list S3 target %s while deleting %s", target.name, relative_key)
            continue
        for row in rows:
            key = row["key"]
            if key == relative_key or key.endswith(f"/{relative_key}"):
                found.append({"target": target, "key": key})
    return found


def _delete_remote_copies(db: Session, relative_key: str) -> list[str]:
    removed = []
    for item in _remote_copies(db, relative_key):
        target = item["target"]
        try:
            backup.delete_s3_object(
                endpoint=target.s3_endpoint,
                region=target.s3_region,
                bucket=target.s3_bucket,
                access_key=target.s3_access_key,
                secret_key=_decrypted(target.s3_secret_key, "S3 secret key"),
                key=item["key"],
                prefix=target.remote_path,
                use_path_style=bool(target.s3_use_path_style),
            )
            removed.append(f"{target.name}:{item['key']}")
        except Exception as exc:
            logger.warning("Could not delete %s from %s: %s", item["key"], target.name, exc)
    return removed


@router.get("/backup-remote-copies")
def find_remote_copies(
    backup_file: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What the confirm dialog needs to name the offsite copies before asking."""
    ensure_role(current_user.role, Role.admin)
    return {
        "items": [
            {"target": item["target"].name, "bucket": item["target"].s3_bucket, "key": item["key"]}
            for item in _remote_copies(db, _remote_relative_key(backup_file))
        ]
    }


@router.delete("/user-backups")
def delete_user_backup(
    backup_file: str,
    request: Request,
    # Delete removes both copies. A backup rotating daily against a fixed
    # retention leaves a copy the panel cannot reach, and the bucket fills.
    also_remote: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    relative_key = _remote_relative_key(backup_file)
    try:
        deleted = backup.delete_user_backup(backup_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    removed_remote = _delete_remote_copies(db, relative_key) if also_remote else []
    log_action(db, current_user.id, "delete_user_backup", "user", deleted, request=request)
    return {"deleted": deleted, "removed_remote": removed_remote}


@router.get("/backup-schedules", response_model=list[BackupScheduleOut])
def list_backup_schedules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return db.query(BackupSchedule).order_by(BackupSchedule.id.desc()).all()


@router.post("/backup-schedules", response_model=BackupScheduleOut)
def create_backup_schedule(payload: BackupScheduleCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    user_ids = [] if payload.all_users else (payload.user_ids or ([payload.user_id] if payload.user_id else []))
    user_ids = sorted({int(user_id) for user_id in user_ids if int(user_id) > 0})
    users = []
    if not payload.all_users:
        if not user_ids:
            raise HTTPException(status_code=400, detail="Select at least one user")
        users = db.query(User).filter(User.id.in_(user_ids)).all()
        found_ids = {user.id for user in users}
        missing_ids = [str(user_id) for user_id in user_ids if user_id not in found_ids]
        if missing_ids:
            raise HTTPException(status_code=404, detail=f"User not found: {', '.join(missing_ids)}")
    if payload.target_id and not db.query(BackupTarget).filter(BackupTarget.id == payload.target_id, BackupTarget.is_active == True).first():  # noqa: E712
        raise HTTPException(status_code=404, detail="Backup target not found")
    item = BackupSchedule(
        user_id=user_ids[0] if user_ids else None,
        user_ids=json.dumps(user_ids),
        all_users=payload.all_users,
        target_id=payload.target_id,
        schedule=payload.schedule,
        retention=payload.retention,
        is_active=payload.is_active,
        last_status="pending",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    target = "all_users" if payload.all_users else ",".join(user.username for user in users)
    log_action(db, current_user.id, "create_backup_schedule", target, payload.schedule, request=request)
    return item


@router.post("/backup-schedules/{schedule_id}/run")
def run_backup_schedule_now(schedule_id: int, request: Request, db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    """Run a schedule immediately, without waiting for its cron.

    Goes through the same code the timer uses, so a run started here lands on
    the same rotation slot and honours the same retention -- it is the schedule
    running early, not a different kind of backup.
    """
    ensure_role(current_user.role, Role.admin)
    schedule = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Backup schedule not found")
    job = _queue_backup_job(current_user, "schedule_run", "Schedule queued", schedule_id=schedule.id)
    _backup_job_executor.submit(_run_schedule_now_job, job["job_id"], current_user.id, schedule.id)
    log_action(db, current_user.id, "run_backup_schedule", str(schedule.id), schedule.schedule, request=request)
    return job


@router.delete("/backup-schedules/{schedule_id}")
def delete_backup_schedule(schedule_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    item = db.query(BackupSchedule).filter(BackupSchedule.id == schedule_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Backup schedule not found")
    db.delete(item)
    db.commit()
    log_action(db, current_user.id, "delete_backup_schedule", str(schedule_id), request=request)
    return {"ok": True}


@router.get("/backup-targets", response_model=list[BackupTargetOut])
def list_backup_targets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return db.query(BackupTarget).order_by(BackupTarget.id.desc()).all()


@router.post("/backup-targets", response_model=BackupTargetOut)
def create_backup_target(
    payload: BackupTargetCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    if db.query(BackupTarget).filter(BackupTarget.name == payload.name).first():
        raise HTTPException(status_code=409, detail="Backup target name already exists")

    target = BackupTarget(
        name=payload.name,
        kind=payload.kind,
        remote_path=payload.remote_path,
        is_active=True,
        # SFTP
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password=encrypt(payload.password) if payload.password else None,
        private_key=encrypt(payload.private_key) if payload.private_key else None,
        # S3 compatible
        s3_endpoint=payload.s3_endpoint,
        s3_region=payload.s3_region,
        s3_bucket=payload.s3_bucket,
        s3_access_key=payload.s3_access_key,
        s3_secret_key=encrypt(payload.s3_secret_key) if payload.s3_secret_key else None,
        s3_use_path_style=payload.s3_use_path_style,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    log_action(db, current_user.id, "create_backup_target", f"{target.kind}:{target.name}", request=request)
    return target


@router.post("/backup-targets/{target_id}/test")
def test_backup_target(
    target_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Prove the target can be written to before a schedule depends on it.

    Endpoint, region and path-style addressing are easy to get wrong and all
    three fail in ways that look like something else, so this writes a small
    object and deletes it rather than only checking that the bucket exists.
    """
    ensure_role(current_user.role, Role.admin)
    target = db.query(BackupTarget).filter(BackupTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Backup target not found")
    if (target.kind or "sftp") != "s3":
        raise HTTPException(
            status_code=400,
            detail="Only S3 targets can be tested. Run a backup to verify an SFTP target.",
        )
    try:
        backup.test_s3_target(
            endpoint=target.s3_endpoint,
            region=target.s3_region,
            bucket=target.s3_bucket,
            access_key=target.s3_access_key,
            secret_key=_decrypted(target.s3_secret_key, "S3 secret key"),
            use_path_style=bool(target.s3_use_path_style),
            prefix=target.remote_path,
        )
    except backup.S3Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "message": f"Wrote and removed a test object in {target.s3_bucket}."}


@router.get("/backup-targets/{target_id}/objects")
def list_backup_target_objects(
    target_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What this destination is actually holding.

    Without this the panel could send a backup to S3 and never mention it
    again: nothing listed the bucket, so nothing could be restored from it or
    removed by hand.
    """
    ensure_role(current_user.role, Role.admin)
    target = db.query(BackupTarget).filter(BackupTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Backup target not found")
    if (target.kind or "sftp") != "s3":
        raise HTTPException(status_code=400, detail="Only S3 destinations can be listed.")
    try:
        rows = backup.list_s3_backups(
            endpoint=target.s3_endpoint,
            region=target.s3_region,
            bucket=target.s3_bucket,
            access_key=target.s3_access_key,
            secret_key=_decrypted(target.s3_secret_key, "S3 secret key"),
            prefix=target.remote_path,
            use_path_style=bool(target.s3_use_path_style),
        )
    except backup.S3Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "bucket": target.s3_bucket,
        "prefix": target.remote_path,
        "items": [
            {
                "key": row["key"],
                "name": row["key"].rsplit("/", 1)[-1],
                "size": row["size"],
                "modified": row["modified"].isoformat() if row.get("modified") else "",
            }
            for row in rows
        ],
    }


@router.delete("/backup-targets/{target_id}/objects")
def delete_backup_target_object(
    target_id: int,
    key: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    target = db.query(BackupTarget).filter(BackupTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Backup target not found")
    if (target.kind or "sftp") != "s3":
        raise HTTPException(status_code=400, detail="Only S3 destinations can be deleted from.")
    try:
        deleted = backup.delete_s3_object(
            endpoint=target.s3_endpoint,
            region=target.s3_region,
            bucket=target.s3_bucket,
            access_key=target.s3_access_key,
            secret_key=_decrypted(target.s3_secret_key, "S3 secret key"),
            key=key,
            prefix=target.remote_path,
            use_path_style=bool(target.s3_use_path_style),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except backup.S3Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "delete_s3_backup", target.name, deleted, request=request)
    return {"deleted": deleted}


@router.delete("/backup-targets/{target_id}")
def delete_backup_target(
    target_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    target = db.query(BackupTarget).filter(BackupTarget.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Backup target not found")
    name = target.name
    db.delete(target)
    db.commit()
    log_action(db, current_user.id, "delete_backup_target", name, request=request)
    return {"ok": True}


@router.post("/backup-sftp")
def create_sftp_backup(
    payload: SftpBackupRun,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    website = get_owned_website(db, current_user, payload.website_id)
    target = db.query(BackupTarget).filter(BackupTarget.id == payload.target_id).first()
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="Backup target not found")
    log_action(db, current_user.id, "queue_backup_sftp", website.domain, target.name, request=request)
    return _queue_sftp_backup(current_user, website, target.id)


@router.get("/php-config")
def get_php_config(php_version: str = Query(default="8.4"), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        return php.read_php_ini(php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/php-config")
def update_php_config(payload: PhpConfigUpdate, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        target = php.update_php_ini(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"target": target}


@router.post("/php-config/defaults")
def restore_php_config_defaults(payload: PhpConfigRestore, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        target = php.restore_default_php_ini(payload.php_version)
        values = php.default_php_config(payload.php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"target": target, "values": values}


@router.get("/php-versions")
def get_php_versions(current_user: User = Depends(get_current_user)):
    # Every user needs this to pick a PHP version for their own site, so it is
    # not admin-only. Installing a version below still is.
    return {
        "installed": php.list_installed_php(),
        "supported": list(php.SUPPORTED_PHP_VERSIONS),
    }


@router.post("/php-versions/{php_version}/install")
def install_php_version(php_version: str, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        result = php.install_php(php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result


# ---------------------------------------------------------------------------
# PHP / LSPHP auto-tuning
# ---------------------------------------------------------------------------
@router.get("/php/tuning")
def get_php_tuning(
    php_version: str = Query(default="8.4"),
    current_user: User = Depends(get_current_user),
):
    """Return current OPanel PHP config + hardware recommendation."""
    ensure_role(current_user.role, Role.admin)
    try:
        current = php.read_php_tuning(php_version)
        recommendation = php.recommend_php_config()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"current": current, "recommendation": recommendation}


@router.post("/php/tuning")
def apply_php_tuning_endpoint(
    php_version: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Auto-tune PHP/LSPHP for current hardware and restart OLS.

    If php_version is omitted, applies to ALL installed LSPHP versions.
    """
    ensure_role(current_user.role, Role.admin)
    try:
        result = php.apply_php_tuning(php_version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "php_tuning_apply",
               ",".join(result["applied_to"]),
               detail=f"mem={result['memory_limit']} opcache={result['opcache_memory_consumption']}M workers={result['lsapi_children']}")
    return result


@router.post("/cron")
def add_cron(payload: CronCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    cron_user = cron.cron_user_for_website(website)
    if not website.linux_user and cron_user != "www-data":
        website.linux_user = cron_user
        db.add(website)
    try:
        line = cron.add_cron(website, payload.schedule, payload.command)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    log_action(db, current_user.id, "add_cron", website.domain, line)
    return {"line": line, "cron_user": cron_user}


@router.get("/cron/{website_id}")
def list_cron(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    cron_user = cron.cron_user_for_website(website)
    return {"items": cron.list_cron_entries(website.domain, cron_user), "cron_user": cron_user}


@router.delete("/cron")
def delete_cron(payload: CronDelete, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    cron_user = cron.cron_user_for_website(website)
    try:
        line = cron.delete_cron(website.domain, payload.index, cron_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "delete_cron", website.domain, line)
    return {"deleted": line, "cron_user": cron_user}


@router.post("/wordpress")
def wordpress_action(payload: WpAction, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, payload.website_id)
    result = wordpress.wp_update(
        str(site_users.document_root(website.root_path, website.document_root or "public_html")),
        payload.action,
        site_users.require_site_linux_user(website),
    )
    return result.__dict__


@router.post("/wordpress/{website_id}/fix-permissions")
def fix_wordpress_permissions(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    if website.linux_user:
        runtime_php_version = website.php_version if (website.app_type or "wordpress") in {"wordpress", "php"} else None
        site_users.ensure_site_runtime(website.domain, website.root_path, runtime_php_version, website.linux_user)
    wordpress.fix_permissions(website.root_path, website.linux_user)
    log_action(db, current_user.id, "fix_permissions", website.domain, website.root_path)
    return {"message": f"Fixed permissions for {website.domain}", "root_path": website.root_path}

class WpInstallRequest(BaseModel):
    admin_user: str = "admin"
    admin_email: str = ""
    admin_password: str = ""
    title: str = ""

@router.post("/wordpress/{website_id}/install")
def install_wordpress_on_site(website_id: int, payload: WpInstallRequest, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Install WordPress on an existing website that doesn't have it yet."""
    from pydantic import EmailStr
    website = get_owned_website(db, current_user, website_id)
    if not payload.admin_email:
        payload.admin_email = f"admin@{website.domain}"
    if not payload.admin_password or len(payload.admin_password) < 12:
        raise HTTPException(status_code=400, detail="admin_password must be at least 12 characters")
    if not payload.title:
        payload.title = website.domain

    # Check if WP already installed (wp-config.php exists)
    from pathlib import Path
    wp_config = Path(website.root_path) / (website.document_root or "public_html") / "wp-config.php"
    if wp_config.exists():
        raise HTTPException(status_code=400, detail="WordPress is already installed on this site")

    # Create database
    try:
        db_info = mariadb.create_database(website.domain)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"Could not create database: {exc}") from exc

    # Install WordPress
    try:
        wordpress.install_wordpress(
            website.domain, db_info, payload.title,
            payload.admin_user, payload.admin_password, payload.admin_email,
            website.php_version, website.linux_user, root_path=website.root_path,
        )
    except (RuntimeError, ValueError) as exc:
        mariadb.drop_database(db_info["db_name"], db_info["db_user"])
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Update website app_type
    website.app_type = "wordpress"
    website.nginx_rewrite_mode = "front_controller"
    db.commit()

    # Rewrite vhost
    try:
        openlitespeed.rewrite_vhost(
            website.domain, website.root_path,
            app_type="wordpress", php_version=website.php_version,
            linux_user=website.linux_user,
            lsphp_socket_override=site_users.site_lsphp_socket(website.linux_user, website.root_path, website.php_version),
            document_root=website.document_root or "public_html",
            rewrite_mode="front_controller",
        )
    except (RuntimeError, ValueError):
        pass

    log_action(db, current_user.id, "install_wordpress", website.domain, request=request)
    return {"message": f"WordPress installed on {website.domain}", "admin_user": payload.admin_user, "admin_password": payload.admin_password}

@router.post("/wordpress/{website_id}/update-all")
def update_wordpress_all(website_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Update WordPress core, plugins, and themes."""
    website = get_owned_website(db, current_user, website_id)
    doc_root = str(site_users.document_root(website.root_path, website.document_root or "public_html"))
    results = {}
    for action in ("core", "plugins", "themes"):
        try:
            result = wordpress.wp_update(doc_root, action, site_users.require_site_linux_user(website))
            results[action] = (result.stdout or result.stderr or "").strip()
        except (RuntimeError, ValueError) as exc:
            results[action] = str(exc)
    log_action(db, current_user.id, "update_wordpress_all", website.domain, request=request)
    return {"message": f"WordPress updated on {website.domain}", "results": results}


@router.get("/files/jobs")
def list_file_jobs(website_id: int | None = Query(default=None), current_user: User = Depends(get_current_user)):
    return {"jobs": _list_file_jobs(current_user, website_id)}


@router.get("/files/jobs/{job_id}")
def get_file_job(job_id: str, current_user: User = Depends(get_current_user)):
    job = _get_file_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="File job not found")
    if job.get("user_id") != current_user.id and not is_admin_role(current_user.role):
        raise HTTPException(status_code=403, detail="Access denied")
    return _public_file_job(job)


@router.get("/files/{website_id}")
def list_files(website_id: int, path: str = Query(default=""), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    return {"items": file_manager.list_files(website, path)}


@router.get("/files/{website_id}/read")
def read_file(website_id: int, path: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        content = file_manager.read_text_file(
            website,
            path,
            allow_sensitive=is_admin_role(current_user.role),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"content": content}


@router.get("/files/{website_id}/download")
def download_file(website_id: int, path: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = get_owned_website(db, current_user, website_id)
    try:
        # Opened here, not by FileResponse later: FileResponse opens the path
        # lazily at send time, after this function returns, and the tenant owns
        # the tree it points into. See file_manager.download_file_handle.
        handle, name, size = file_manager.download_file_handle(
            website, path, allow_sensitive=is_admin_role(current_user.role)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _stream():
        try:
            while chunk := handle.read(64 * 1024):
                yield chunk
        finally:
            handle.close()

    return StreamingResponse(
        _stream(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(size),
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


@router.post("/files/mkdir")
def make_directory(payload: FileMkdir, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        target = file_manager.make_directory(website, payload.path, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "mkdir", website.domain, target)
    return {"target": target}


@router.post("/files/create")
def create_file(payload: FileCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        target = file_manager.create_text_file(
            website,
            payload.path,
            payload.name,
            is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "create_file", website.domain, target)
    return {"target": target}


@router.post("/files/rename")
def rename_entry(payload: FileRename, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        target = file_manager.rename_entry(website, payload.path, payload.new_name, is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "rename_file", website.domain, target)
    return {"target": target}


@router.post("/files/chmod")
def chmod_entry(payload: FileChmod, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        target = file_manager.chmod_entry(website, payload.path, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "chmod_file", website.domain, f"{target} {payload.mode}")
    return {"target": target, "mode": payload.mode}


@router.post("/files/delete")
def delete_entries(payload: FileBulkDelete, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        deleted = file_manager.delete_entries(website, payload.paths, is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "delete_files", website.domain, ",".join(payload.paths[:20]))
    return {"deleted": deleted}


@router.post("/files/copy")
def copy_entries(payload: FileTransfer, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        copied = file_manager.copy_entries(
            website,
            payload.paths,
            payload.destination_path,
            allow_executable=is_admin_role(current_user.role),
            allow_sensitive=is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "copy_files", website.domain, f"{','.join(payload.paths[:20])} -> {payload.destination_path}")
    return {"copied": copied}


@router.post("/files/move")
def move_entries(payload: FileTransfer, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        moved = file_manager.move_entries(
            website,
            payload.paths,
            payload.destination_path,
            allow_executable=is_admin_role(current_user.role),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "move_files", website.domain, f"{','.join(payload.paths[:20])} -> {payload.destination_path}")
    return {"moved": moved}


@router.post("/files/archive")
def archive_entries(payload: FileArchive, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        target = file_manager.archive_entries(
            website,
            payload.base_path,
            payload.paths,
            payload.output_name,
            payload.format,
            allow_sensitive=is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "archive_files", website.domain, target)
    return {"target": target}


@router.post("/files/extract")
def extract_archive(payload: FileExtract, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    # Validate archive format before queuing to catch bad archives early
    archive_file = file_manager._safe_path(website, payload.archive_path)  # noqa: SLF001
    if not archive_file.exists() or not archive_file.is_file():
        raise HTTPException(status_code=400, detail="Archive not found")
    if archive_file.is_symlink():
        raise HTTPException(status_code=400, detail="Symlinks are not allowed")
    suffix = archive_file.name.lower()
    if not (suffix.endswith(".zip") or suffix.endswith(".tar.gz") or suffix.endswith(".tgz")):
        raise HTTPException(status_code=400, detail="Only .zip, .tar.gz, and .tgz archives are supported")
    # Validate archive is readable and not corrupted
    try:
        if suffix.endswith(".zip"):
            with zipfile.ZipFile(archive_file) as _:
                pass
        else:
            with tarfile.open(archive_file, "r:gz") as _:
                pass
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP archive") from None
    except tarfile.TarError:
        raise HTTPException(status_code=400, detail="Invalid or corrupted tar archive") from None
    job = _queue_extract_job(
        current_user,
        website,
        payload.archive_path,
        payload.destination_path,
        True,
    )
    log_action(db, current_user.id, "extract_archive_queued", website.domain, payload.archive_path)
    return {**job, "message": "Extraction started in the background"}


@router.post("/files/write")
def write_file(payload: FileWrite, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, payload.website_id)
    try:
        target = file_manager.write_text_file(
            website,
            payload.path,
            payload.content,
            is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"target": target}


@router.post("/files/{website_id}/upload")
def upload_file(
    website_id: int,
    path: str = Query(default=site_users.PUBLIC_DIR),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, website_id)
    try:
        target = file_manager.upload_file(
            website,
            path,
            file.filename or "upload.bin",
            file.file,
            is_admin_role(current_user.role),
            quota_check=_quota_check_for_website(db, website),
        )
    except storage_quota.StorageQuotaExceeded as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "upload_file", website.domain, target)
    return {"target": target}


@router.delete("/files/{website_id}")
def delete_file(website_id: int, path: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    website = get_owned_website(db, current_user, website_id)
    try:
        target = file_manager.delete_file(website, path, is_admin_role(current_user.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": target}
