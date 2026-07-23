import os
import stat
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.permissions import is_admin_role
from app.models.entities import User, Website


BYTES_PER_MB = 1024 * 1024
STATIC_SITE_ESTIMATE_BYTES = 1 * BYTES_PER_MB
WORDPRESS_SITE_ESTIMATE_BYTES = 100 * BYTES_PER_MB


class StorageQuotaExceeded(ValueError):
    pass


def user_storage_limit_bytes(user: User) -> int | None:
    if is_admin_role(user.role):
        return None
    return max(0, int(user.storage_limit_mb or 0)) * BYTES_PER_MB


def path_usage_bytes(path: str | Path) -> int:
    root = Path(path)
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            item_stat = current.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(item_stat.st_mode):
            continue
        total += item_stat.st_size
        if stat.S_ISDIR(item_stat.st_mode):
            try:
                stack.extend(current.iterdir())
            except OSError:
                continue
    return total


def website_storage_used_bytes(website: Website) -> int:
    if not website.root_path:
        return 0
    return path_usage_bytes(website.root_path)


def user_storage_used_bytes(db: Session, user: User) -> int:
    websites = db.query(Website).filter(Website.owner_id == user.id).all()
    return sum(website_storage_used_bytes(website) for website in websites)


def storage_usage_summary(db: Session, user: User) -> dict:
    used_bytes = user_storage_used_bytes(db, user)
    limit_bytes = user_storage_limit_bytes(user)
    percent = 0.0
    if limit_bytes and limit_bytes > 0:
        percent = min(999.0, round((used_bytes / limit_bytes) * 100, 2))
    return {
        "storage_used_bytes": used_bytes,
        "storage_limit_bytes": limit_bytes,
        "storage_percent": percent,
    }


def enforce_user_storage_quota(
    db: Session,
    user: User,
    *,
    incoming_bytes: int = 0,
    replaced_bytes: int = 0,
) -> None:
    limit_bytes = user_storage_limit_bytes(user)
    if limit_bytes is None:
        return
    used_bytes = user_storage_used_bytes(db, user)
    projected_bytes = max(0, used_bytes - max(0, replaced_bytes)) + max(0, incoming_bytes)
    if projected_bytes > limit_bytes:
        raise StorageQuotaExceeded(
            f"Storage quota exceeded: {projected_bytes // BYTES_PER_MB} MB used/projected, "
            f"limit {limit_bytes // BYTES_PER_MB} MB"
        )


def source_file_size(source_file) -> int | None:
    try:
        position = source_file.tell()
        source_file.seek(0, os.SEEK_END)
        size = source_file.tell()
        source_file.seek(position)
        return max(0, int(size - position))
    except (AttributeError, OSError, ValueError):
        return None
