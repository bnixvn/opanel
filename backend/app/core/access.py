"""Who may see and manage which accounts.

Three roles share one rule set:

- an admin manages every account on the server;
- a reseller manages itself and its customers, the end users whose
  ``reseller_id`` points at it, and nothing else;
- an end user manages only itself.

Endpoints ask here rather than comparing ``owner_id`` with the caller's id and
falling back to "admin": that fallback is what would have let a reseller into
every customer's site, or locked it out of its own customers'. A reseller is
never treated as an admin: server-wide pages keep ``ensure_role(..., Role.admin)``.
"""

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import is_admin_role, is_reseller_role
from app.models.entities import User


def customer_ids(db: Session, reseller: User) -> set[int]:
    rows = db.query(User.id).filter(User.reseller_id == reseller.id).all()
    return {row[0] for row in rows}


def managed_user_ids(db: Session, actor: User) -> Optional[set[int]]:
    """The accounts ``actor`` manages, or None for "all of them" (an admin)."""
    if is_admin_role(actor.role):
        return None
    if is_reseller_role(actor.role):
        return {actor.id} | customer_ids(db, actor)
    return {actor.id}


def can_manage_user(db: Session, actor: User, target: Optional[User]) -> bool:
    if target is None:
        return False
    if is_admin_role(actor.role):
        return True
    if target.id == actor.id:
        return True
    return is_reseller_role(actor.role) and target.reseller_id == actor.id


def is_customer_of(actor: User, target: User) -> bool:
    """A reseller looking at one of its customers (not at itself)."""
    return is_reseller_role(actor.role) and target.id != actor.id and target.reseller_id == actor.id


def managed_user(db: Session, actor: User, user_id: int) -> User:
    """The account, if ``actor`` manages it. Anyone else's reads as not found."""
    target = db.query(User).filter(User.id == user_id).first()
    if not can_manage_user(db, actor, target):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target


def can_access_owner(db: Session, actor: User, owner_id: Optional[int]) -> bool:
    """Whether ``actor`` may manage a resource that ``owner_id`` owns."""
    if is_admin_role(actor.role):
        return True
    if owner_id is None:
        return False
    if owner_id == actor.id:
        return True
    if not is_reseller_role(actor.role):
        return False
    owner = db.query(User.reseller_id).filter(User.id == owner_id).first()
    return bool(owner) and owner[0] == actor.id


def ensure_owner_access(db: Session, actor: User, owner_id: Optional[int]) -> None:
    if not can_access_owner(db, actor, owner_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")


def scope_owner(query, column, db: Session, actor: User):
    """Narrow a query to the rows ``actor`` may see, by their owner column."""
    ids = managed_user_ids(db, actor)
    if ids is None:
        return query
    return query.filter(column.in_(sorted(ids)))
