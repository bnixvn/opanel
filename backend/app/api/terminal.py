"""The browser terminal (operator, 2026-10-17): a real shell over a WebSocket.

GET  /terminal/info          whether this account may open one, and the rules
WS   /terminal/ws            a shell in the account's home
WS   /terminal/ws/{site_id}  a shell in the website's directory, with its PHP

On the socket the browser sends keystrokes as binary frames and control
messages as JSON text ({"type": "resize", "cols", "rows"}); the panel sends the
terminal's output as binary frames and its own messages as JSON text:
{"type": "ready", ...} once the shell runs, {"type": "closed", "reason", "code"}
just before it closes the socket. services/terminal.py holds the rules and the
sandbox.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.deps import _user_from_token, get_current_user
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.entities import RevokedToken, User, Website
from app.services import resource_limits, site_users, terminal
from app.services.audit import log_action

logger = logging.getLogger("opanel.terminal")
router = APIRouter(prefix="/terminal", tags=["terminal"])

# How often a running session checks that its account may still have it.
RECHECK_SECONDS = 15
# Output waiting for a slow browser; past this the PTY is not read until it drains.
MAX_QUEUED_CHUNKS = 64
MAX_INPUT_BYTES = 64 * 1024


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if not origin:
        return True
    origin_host = (urlparse(origin).netloc or "").lower()
    request_host = (websocket.headers.get("host") or "").lower()
    if origin_host and request_host and origin_host == request_host:
        return True
    allowed = {item.rstrip("/") for item in settings.cors_origins}
    if settings.panel_url:
        allowed.add(settings.panel_url.rstrip("/"))
    return bool(allowed) and origin in allowed


def _rules() -> dict:
    return {"max_sessions": terminal.MAX_SESSIONS_PER_ACCOUNT,
            "idle_minutes": terminal.IDLE_SECONDS // 60,
            "max_hours": terminal.MAX_SECONDS // 3600}


@router.get("/info")
def terminal_info(current_user: User = Depends(get_current_user)):
    problem = terminal.access_problem(current_user)
    return {"allowed": problem is None, "problem": problem,
            "detail": terminal.PROBLEM_TEXT.get(problem) if problem else None,
            "sessions": len(terminal.sessions_of(current_user.id)), **_rules()}


def _still_allowed(user_id: int, payload: dict) -> bool:
    """The browser session that opened the shell is still signed in, and the
    account may still have a shell. Expiry is left out: idle and the eight
    hours are the session's own limits, and a sliding sign-in renews the
    cookie, not the token this socket was opened with."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None or (user.token_version or 0) != int(payload.get("tv", 0)):
            return False
        jti = payload.get("jti")
        if jti and db.query(RevokedToken.id).filter(RevokedToken.jti == jti).first():
            return False
        return terminal.access_problem(user) is None
    finally:
        db.close()


def _audit(user_id: int, action: str, target: str, detail: str, websocket: WebSocket) -> None:
    db = SessionLocal()
    try:
        log_action(db, user_id, action, target, detail, request=websocket)
    finally:
        db.close()


async def _refuse(websocket: WebSocket, code: int, reason: str) -> None:
    # Accept first: a close before the handshake reaches the browser as a bare
    # 1006, and the page could not say why.
    await websocket.accept()
    await websocket.send_json({"type": "closed", "reason": reason, "code": code})
    await websocket.close(code=code, reason=reason[:120])


@router.websocket("/ws")
async def terminal_home(websocket: WebSocket):
    await _serve(websocket, None)


@router.websocket("/ws/{website_id}")
async def terminal_website(websocket: WebSocket, website_id: int):
    await _serve(websocket, website_id)


async def _serve(websocket: WebSocket, website_id: int | None) -> None:
    if not _origin_allowed(websocket):
        await _refuse(websocket, 4003, "origin")
        return
    token = websocket.cookies.get("opanel_session")
    if not token:
        await _refuse(websocket, 4001, "signed-out")
        return

    db = SessionLocal()
    try:
        try:
            user, payload = _user_from_token(token, db, websocket)
        except HTTPException:
            await _refuse(websocket, 4001, "signed-out")
            return
        problem = terminal.access_problem(user)
        if problem:
            await _refuse(websocket, 4003, problem)
            return
        if website_id is None:
            linux_user = site_users.linux_user_for_panel_username(user.username)
            cwd, php_version, target = f"/home/{linux_user}", None, "home"
        else:
            website = db.query(Website).filter(Website.id == website_id).first()
            # A customer's own websites only: no one reaches this with another role.
            if website is None or website.owner_id != user.id:
                await _refuse(websocket, 4004, "not-found")
                return
            try:
                linux_user = site_users.require_site_linux_user(website)
                cwd = terminal.default_cwd(website.root_path)
            except (ValueError, RuntimeError):
                await _refuse(websocket, 4004, "not-found")
                return
            php_version, target = website.php_version, website.domain
        user_id, impersonator = user.id, str(payload.get("imp") or "")
        slice_name = resource_limits.account_slice(user)
    finally:
        db.close()

    if not terminal.available():
        await _refuse(websocket, 4005, "unavailable")
        return
    session = terminal.reserve(user_id, linux_user, target)
    if session is None:
        await _refuse(websocket, 4008, "too-many")
        return

    await websocket.accept()
    cols, rows = terminal.clamp_size(websocket.query_params.get("cols"), websocket.query_params.get("rows"))
    try:
        process, master = terminal.spawn(session, cwd, php_version, slice_name, cols, rows)
    except Exception:
        logger.exception("terminal %s did not start", session.unit)
        terminal.release(session)
        await websocket.send_json({"type": "closed", "reason": "failed", "code": 4500})
        await websocket.close(code=4500)
        return

    detail = f"session={session.id} user={linux_user} cwd={cwd}"
    if impersonator:
        detail += f" by={impersonator}"
    await asyncio.to_thread(_audit, user_id, "terminal_open", target, detail, websocket)
    started = time.monotonic()
    reason = "closed"
    try:
        reason = await _bridge(websocket, session, process, master, payload, cwd, php_version)
    finally:
        await asyncio.to_thread(terminal.hang_up, process, master)
        await asyncio.to_thread(terminal.stop_unit, session.unit)
        terminal.release(session)
        code = terminal.exit_status(process)
        if websocket.application_state == WebSocketState.CONNECTED and websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.send_json({"type": "closed", "reason": reason, "code": code})
                await websocket.close(code=1000)
            except Exception:
                pass
        minutes = round((time.monotonic() - started) / 60, 1)
        await asyncio.to_thread(_audit, user_id, "terminal_close", target,
                                f"session={session.id} reason={reason} minutes={minutes}", websocket)


async def _bridge(websocket, session, process, master, payload, cwd, php_version) -> str:
    """Copy between the socket and the PTY until one side ends; returns why."""
    loop = asyncio.get_running_loop()
    output: asyncio.Queue = asyncio.Queue()
    closing: asyncio.Future = loop.create_future()
    reading = True

    def request_close(reason: str) -> None:
        def settle():
            if not closing.done():
                closing.set_result(reason)
        loop.call_soon_threadsafe(settle)

    session.close_request = request_close

    def on_readable():
        nonlocal reading
        try:
            data = os.read(master, 65536)
        except BlockingIOError:
            return
        except OSError:  # EIO: the shell has gone
            data = b""
        if not data:
            loop.remove_reader(master)
            reading = False
            output.put_nowait(None)
            return
        output.put_nowait(data)
        if output.qsize() > MAX_QUEUED_CHUNKS:
            loop.remove_reader(master)
            reading = False

    loop.add_reader(master, on_readable)

    async def send_output():
        nonlocal reading
        while True:
            data = await output.get()
            if data is None:
                return "exit"
            while not output.empty() and len(data) < 256 * 1024:
                more = output.get_nowait()
                if more is None:
                    await websocket.send_bytes(data)
                    return "exit"
                data += more
            await websocket.send_bytes(data)
            # Read again once the browser has caught up, even if the shell has
            # exited meanwhile: what it wrote last, and the end, are still there.
            if not reading and output.qsize() < MAX_QUEUED_CHUNKS // 4:
                reading = True
                loop.add_reader(master, on_readable)

    async def write_all(data: bytes):
        view = memoryview(data)
        while view:
            try:
                written = os.write(master, view)
                view = view[written:]
            except BlockingIOError:
                await asyncio.sleep(0.02)

    async def receive_input():
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return "closed"
            data = message.get("bytes")
            if data is not None:
                session.last_input = time.monotonic()
                await write_all(data[:MAX_INPUT_BYTES])
                continue
            try:
                control = json.loads(message.get("text") or "")
            except ValueError:
                continue
            if not isinstance(control, dict):
                continue
            if control.get("type") == "resize":
                terminal.set_size(master, control.get("cols"), control.get("rows"))

    async def watch():
        while True:
            await asyncio.sleep(RECHECK_SECONDS)
            now = time.monotonic()
            if now - session.started >= terminal.MAX_SECONDS:
                return "max-time"
            if now - session.last_input >= terminal.IDLE_SECONDS:
                return "idle"
            if not await asyncio.to_thread(_still_allowed, session.user_id, payload):
                return "access-changed"

    await websocket.send_json({"type": "ready", "cwd": cwd, "php": php_version or None,
                               "user": session.linux_user, **_rules()})
    tasks = [asyncio.ensure_future(job) for job in (send_output(), receive_input(), watch(), closing)]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finished = next(iter(done))
        try:
            return finished.result()
        except WebSocketDisconnect:
            return "closed"
        except Exception:
            logger.exception("terminal %s failed", session.unit)
            return "failed"
    finally:
        session.close_request = None
        if reading:
            loop.remove_reader(master)
        for task in tasks:
            task.cancel()
