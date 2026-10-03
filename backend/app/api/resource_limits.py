"""Resource limits addon: what each account may use and is using."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.access import managed_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import resource_limits

router = APIRouter(prefix="/resource-limits", tags=["resource-limits"])


@router.get("")
def overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Limits and current use: every account for an admin, a reseller's own
    and its customers' for a reseller, an end user's own."""
    ensure_role(current_user.role, Role.end_user)
    return resource_limits.overview(db, current_user)


@router.get("/{user_id}/history")
def history(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The last day (5-minute points) and week (hourly) of one account."""
    ensure_role(current_user.role, Role.end_user)
    return resource_limits.history(managed_user(db, current_user, user_id))
