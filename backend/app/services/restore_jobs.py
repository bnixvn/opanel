"""Restores as records: what was asked, how each archive went, and a retry (as SNPanel's, 2026-10-17).

The restore itself runs as before, with its live progress in the backup task
list. Each run is also a row here, its archives each marked pending, running,
done (with what came back) or failed (with why), so the outcome outlives the
panel's memory. A row still "running" that this process is not running was
cut short by a restart and reads as interrupted. Failed and interrupted
archives can be run again - from this server or a saved destination; a
server typed into the wizard was never stored, so that one starts over there.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.entities import RestoreJob

KEEP = 50
_live: set[int] = set()
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.utcnow()


def create(db: Session, *, user_id: int, source_kind: str, target_id: Optional[int], label: str,
           overwrite: bool, items: list[dict], retry_of: Optional[int] = None) -> RestoreJob:
    row = RestoreJob(
        created_by=user_id, source_kind=source_kind, target_id=target_id, source_label=label[:255],
        overwrite=bool(overwrite), status="queued", retry_of=retry_of, created_at=_now(),
        items=json.dumps([{
            "kind": item["kind"], "ref": item["ref"],
            "name": item["ref"].replace("\\", "/").rsplit("/", 1)[-1],
            "status": "pending", "note": "", "error": "",
        } for item in items]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _prune(db)
    with _lock:
        _live.add(row.id)
    return row


def _prune(db: Session) -> None:
    old = db.query(RestoreJob.id).order_by(RestoreJob.id.desc()).offset(KEEP).all()
    if old:
        db.query(RestoreJob).filter(RestoreJob.id.in_([row.id for row in old])).delete(synchronize_session=False)
        db.commit()


def _update(job_id: int, change) -> None:
    db = SessionLocal()
    try:
        row = db.get(RestoreJob, job_id)
        if row is None:
            return
        change(row)
        db.commit()
    finally:
        db.close()


def started(job_id: int) -> None:
    def change(row):
        row.status = "running"
        row.started_at = _now()
    _update(job_id, change)


def item(job_id: int, index: int, **fields) -> None:
    def change(row):
        items = json.loads(row.items or "[]")
        if 0 <= index < len(items):
            items[index].update({key: (str(value)[:2000] if isinstance(value, str) else value) for key, value in fields.items()})
            row.items = json.dumps(items)
    _update(job_id, change)


def finished(job_id: int) -> None:
    def change(row):
        items = json.loads(row.items or "[]")
        failed = any(entry["status"] in {"failed", "interrupted", "pending"} for entry in items)
        row.status = "error" if failed else "done"
        row.finished_at = _now()
    try:
        _update(job_id, change)
    finally:
        with _lock:
            _live.discard(job_id)


def settle_stale(db: Session) -> None:
    """Rows a restart cut short: interrupted, and so is every archive not finished."""
    with _lock:
        live = set(_live)
    stale = db.query(RestoreJob).filter(RestoreJob.status.in_(("queued", "running"))).all()
    changed = False
    for row in stale:
        if row.id in live:
            continue
        items = json.loads(row.items or "[]")
        for entry in items:
            if entry["status"] in {"pending", "running"}:
                entry["status"] = "interrupted"
        row.items = json.dumps(items)
        row.status = "interrupted"
        row.finished_at = row.finished_at or _now()
        changed = True
    if changed:
        db.commit()


def retry_items(row: RestoreJob) -> list[dict]:
    return [{"kind": entry["kind"], "ref": entry["ref"]} for entry in json.loads(row.items or "[]")
            if entry["status"] in {"failed", "interrupted", "pending"}]


def serialize(row: RestoreJob) -> dict:
    items = json.loads(row.items or "[]")
    return {
        "id": row.id, "status": row.status, "source_kind": row.source_kind, "source": row.source_label,
        "overwrite": bool(row.overwrite), "retry_of": row.retry_of,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "finished_at": row.finished_at.isoformat() + "Z" if row.finished_at else None,
        "items": items,
        "done": sum(entry["status"] == "done" for entry in items),
        "failed": sum(entry["status"] in {"failed", "interrupted"} for entry in items),
        # A server typed into the wizard was never stored: that one is run again from there.
        "can_retry": row.status in {"error", "interrupted"} and row.source_kind in {"local", "target"}
                     and bool(retry_items(row)),
    }
