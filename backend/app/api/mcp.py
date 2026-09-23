"""The MCP endpoint and the tokens that open it.

POST /api/mcp speaks MCP's Streamable HTTP transport and authenticates with a
personal MCP token, not a panel session: no cookie, so no CSRF, and nothing an
open browser tab could be tricked into sending. The token routes below are the
opposite -- a signed-in panel session manages its own tokens there.
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_admin_role
from app.models.entities import McpToken, User
from app.services import addons, mcp
from app.services.audit import log_action

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _enabled() -> bool:
    return addons.is_enabled(mcp.ADDON_ID)


# ---------------------------------------------------------------------------
# The protocol endpoint
# ---------------------------------------------------------------------------
def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": message},
                        headers={"WWW-Authenticate": 'Bearer realm="opanel-mcp"'})


def _foreign_origin(request: Request) -> bool:
    """A browser page on another site posting here.

    The specification asks servers to check Origin against DNS rebinding. MCP
    clients are not browsers and send none; a page that does send one must be
    the panel's own.
    """
    origin = request.headers.get("origin")
    if not origin:
        return False
    return urlparse(origin).netloc.lower() != (request.headers.get("host") or "").lower()


def _dispatch(ctx: mcp.Context, body) -> Optional[object]:
    if isinstance(body, list):
        if not body:
            return [mcp._error(None, mcp.INVALID_REQUEST, "Empty batch")]
        replies = [reply for reply in (mcp.handle_message(ctx, item) for item in body) if reply is not None]
        return replies
    return mcp.handle_message(ctx, body)


@router.post("")
@router.post("/", include_in_schema=False)
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    if not _enabled():
        return JSONResponse(status_code=404, content={"detail": "MCP is not enabled on this panel"})
    if _foreign_origin(request):
        return JSONResponse(status_code=403, content={"detail": "Cross-origin requests are not accepted"})
    scheme, _, raw_token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        return _unauthorized("An MCP token is required (Authorization: Bearer <token>)")
    try:
        body = json.loads(await request.body() or b"null")
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content=mcp._error(None, mcp.PARSE_ERROR, "Invalid JSON"))

    found = await run_in_threadpool(mcp.authenticate, db, raw_token.strip())
    if found is None:
        return _unauthorized("The MCP token is invalid, expired or revoked")
    token, user = found
    # Tools issue certificates and restart services; keep that off the event loop.
    client_ip = request.client.host if request.client else ""
    ctx = mcp.Context(db=db, user=user, token=token, client_ip=client_ip)
    reply = await run_in_threadpool(_dispatch, ctx, body)
    if reply is None or reply == []:
        # Only notifications or responses came in: accepted, nothing to say.
        return Response(status_code=202)
    return JSONResponse(content=reply)


@router.get("")
@router.get("/", include_in_schema=False)
def mcp_stream():
    # No server-initiated messages, so no SSE stream to open.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("")
@router.delete("/", include_in_schema=False)
def mcp_end_session():
    # Stateless: there is no session to end.
    return Response(status_code=405, headers={"Allow": "POST"})


# ---------------------------------------------------------------------------
# Tokens, managed from a panel session
# ---------------------------------------------------------------------------
class McpTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    can_write: bool = False
    expires_days: int = Field(default=90, ge=1, le=365)


@router.get("/info")
def mcp_info(current_user: User = Depends(get_current_user)):
    return {
        "enabled": _enabled(),
        "endpoint_path": "/api/mcp",
        "max_tokens": mcp.MAX_TOKENS_PER_USER,
        "can_manage_all": is_admin_role(current_user.role),
    }


@router.get("/tokens")
def list_mcp_tokens(all: bool = False, db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    query = db.query(McpToken)
    if all:
        ensure_role(current_user.role, Role.admin)
    else:
        query = query.filter(McpToken.user_id == current_user.id)
    owners = {user.id: user for user in db.query(User).all()}
    return [mcp.token_out(token, owners.get(token.user_id))
            for token in query.order_by(McpToken.id.desc()).all()]


@router.post("/tokens")
def create_mcp_token(payload: McpTokenCreate, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    if not _enabled():
        raise HTTPException(status_code=409, detail="MCP is not enabled on this panel")
    # No password re-entry: the operator chose to trust the signed-in session
    # here. A token is still shown once, listed with its owner, and revocable
    # by its owner or any admin.
    try:
        token, raw = mcp.create_token(db, current_user, payload.name, payload.can_write, payload.expires_days)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_action(db, current_user.id, "mcp_token_create", token.name,
               "actions allowed" if token.can_write else "read-only", request=request)
    return {**mcp.token_out(token, current_user), "token": raw}


@router.delete("/tokens/{token_id}")
def revoke_mcp_token(token_id: int, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    token = db.query(McpToken).filter(McpToken.id == token_id).first()
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    if token.user_id != current_user.id:
        ensure_role(current_user.role, Role.admin)
    name = token.name
    db.delete(token)
    db.commit()
    log_action(db, current_user.id, "mcp_token_revoke", name, str(token_id), request=request)
    return {"ok": True}


def revoke_all(db: Session) -> int:
    """Every MCP token on the panel, for when the addon is removed."""
    count = db.query(McpToken).delete(synchronize_session=False)
    db.commit()
    return count
