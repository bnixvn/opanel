"""Demo mode: the login page's public list, and the admin's settings."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import demo_mode
from app.services.audit import log_action

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoAccountIn(BaseModel):
    user_id: int = Field(gt=0)
    password: str = Field(min_length=12, max_length=72)


class DemoSettingsIn(BaseModel):
    accounts: list[DemoAccountIn] = Field(default_factory=list, max_length=demo_mode.MAX_ACCOUNTS)
    show_on_login: bool = True


@router.get("/info")
def demo_info(db: Session = Depends(get_db)):
    """Public: the login page asks before anyone has signed in. Empty unless
    Demo mode is on and set to show its accounts."""
    accounts = demo_mode.public_accounts(db)
    return {"enabled": bool(accounts), "accounts": accounts}


@router.get("/settings")
def get_demo_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return demo_mode.describe(db)


@router.put("/settings")
def put_demo_settings(payload: DemoSettingsIn, request: Request, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        result = demo_mode.save(db, [a.model_dump() for a in payload.accounts], payload.show_on_login,
                                current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "demo_mode_settings",
               ", ".join(row["username"] for row in result["accounts"]) or "none",
               "shown on login" if payload.show_on_login else "", request=request)
    return result
