"""The panel's MCP (Model Context Protocol) server.

An AI client -- Claude Code, Cursor, VS Code -- connects to POST /api/mcp with
a personal token and gets a set of tools for reading and operating the panel.
The transport is MCP's Streamable HTTP, answered with plain JSON: every tool
here completes within the request, so there is nothing to stream and no
session to keep.

Three rules shape the tools:

- A token acts as the account that created it. Every tool resolves websites,
  databases and backups through the same ownership checks the panel's own API
  uses, so a customer's token cannot see a neighbour's site, and a tool an end
  user may not use does not appear in their tools/list at all.
- A token is read-only unless it was created with can_write. Tools that change
  anything are hidden from a read-only token, not merely refused.
- Nothing here deletes. The actions are the ones an operator would take
  without a second thought -- run a backup, issue a certificate, switch the
  WAF, restart a service -- and every one is written to the audit log.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.permissions import is_admin_role
from app.core.version import APP_VERSION
from app.models.entities import (
    AuditLog,
    BackupSchedule,
    DatabaseAccount,
    McpToken,
    User,
    Website,
    WebsiteAlias,
)
from app.services.audit import log_action

logger = logging.getLogger("opanel.mcp")

ADDON_ID = "mcp"
TOKEN_PREFIX = "opmcp_"
MAX_TOKENS_PER_USER = 10

# Newest first. A client asking for a version not listed here gets the newest,
# which is what the specification asks a server to answer with.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

SERVER_INSTRUCTIONS = (
    "Tools for the OPanel hosting control panel. Websites are named by domain. "
    "Everything you can see belongs to the account that owns the token, unless "
    "that account is an administrator. Tools that change something are only "
    "offered to a token created with actions allowed; nothing here deletes."
)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_token(db: Session, user: User, name: str, can_write: bool,
                 expires_days: int) -> tuple[McpToken, str]:
    """Mint a token for ``user``. The raw value is returned once, here only."""
    name = (name or "").strip()[:64] or "MCP token"
    if db.query(McpToken).filter(McpToken.user_id == user.id).count() >= MAX_TOKENS_PER_USER:
        raise ValueError(f"An account can hold at most {MAX_TOKENS_PER_USER} MCP tokens; revoke one first")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = McpToken(
        user_id=user.id,
        name=name,
        token_hash=_hash(raw),
        prefix=raw[:12],
        can_write=bool(can_write),
        expires_at=datetime.utcnow() + timedelta(days=max(1, min(int(expires_days), 365))),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token, raw


def authenticate(db: Session, raw: str) -> Optional[tuple[McpToken, User]]:
    """The token and its owner, or None for anything that should not get in.

    An inactive owner is refused here even though the panel's session check
    lets one through for impersonation: an MCP token is a standing credential,
    and a suspended customer's automation should stop with the account.
    """
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    token = db.query(McpToken).filter(McpToken.token_hash == _hash(raw)).first()
    if token is None:
        return None
    now = datetime.utcnow()
    if token.expires_at is not None and token.expires_at <= now:
        return None
    user = db.query(User).filter(User.id == token.user_id).first()
    if user is None or not user.is_active:
        return None
    # A write per request would be one per tool call; a minute is precise
    # enough to answer "is this token still in use".
    if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=1):
        token.last_used_at = now
        db.commit()
    return token, user


def token_out(token: McpToken, owner: Optional[User] = None) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "prefix": token.prefix,
        "can_write": bool(token.can_write),
        "expires_at": token.expires_at.isoformat() + "Z" if token.expires_at else None,
        "last_used_at": token.last_used_at.isoformat() + "Z" if token.last_used_at else None,
        "created_at": token.created_at.isoformat() + "Z" if token.created_at else None,
        "user_id": token.user_id,
        "username": owner.username if owner else None,
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@dataclass
class Context:
    db: Session
    user: User
    token: McpToken

    @property
    def is_admin(self) -> bool:
        return is_admin_role(self.user.role)


class ToolError(Exception):
    """A failure the model should read and can act on."""


@dataclass
class Tool:
    name: str
    title: str
    description: str
    properties: dict
    required: tuple = ()
    handler: Callable[[Context, dict], Any] = None
    admin_only: bool = False
    writes: bool = False

    def visible_to(self, ctx: Context) -> bool:
        if self.admin_only and not ctx.is_admin:
            return False
        if self.writes and not ctx.token.can_write:
            return False
        return True

    def describe(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
            "annotations": {
                "title": self.title,
                "readOnlyHint": not self.writes,
                "destructiveHint": False,
                "idempotentHint": not self.writes,
                "openWorldHint": False,
            },
        }


_TOOLS: dict[str, Tool] = {}


def _tool(name: str, title: str, description: str, properties: Optional[dict] = None,
          required: tuple = (), admin_only: bool = False, writes: bool = False):
    def register(fn):
        _TOOLS[name] = Tool(name, title, description, properties or {}, required, fn, admin_only, writes)
        return fn
    return register


DOMAIN_ARG = {"type": "string", "description": "The website's domain, e.g. example.com"}


def _website(ctx: Context, domain: str) -> Website:
    """A website the caller may act on, by domain. Someone else's is 'not found'."""
    website = ctx.db.query(Website).filter(Website.domain == (domain or "").strip().lower()).first()
    if website is None or (website.owner_id != ctx.user.id and not ctx.is_admin):
        raise ToolError(f"No website {domain!r} on this account")
    return website


def _account(ctx: Context, username: Optional[str]) -> User:
    """The caller, or -- for an administrator -- the named account."""
    if not username or username == ctx.user.username:
        return ctx.user
    if not ctx.is_admin:
        raise ToolError("Only an administrator can act on another account")
    user = ctx.db.query(User).filter(User.username == username).first()
    if user is None:
        raise ToolError(f"No account {username!r}")
    return user


def _iso(value) -> Optional[str]:
    return value.isoformat() + "Z" if isinstance(value, datetime) else value


def _website_summary(ctx: Context, website: Website, owners: dict[int, str]) -> dict:
    return {
        "domain": website.domain,
        "owner": owners.get(website.owner_id, ""),
        "app_type": website.app_type,
        "php_version": website.php_version,
        "status": website.status,
        "ssl_enabled": bool(website.ssl_enabled),
        "ssl_mode": website.ssl_mode,
        "waf_enabled": bool(website.waf_enabled),
    }


@_tool("whoami", "Who am I",
       "The account this token acts as, its limits, and whether the token may take actions.")
def _whoami(ctx: Context, args: dict):
    user = ctx.user
    return {
        "username": user.username,
        "email": user.email,
        "role": "admin" if ctx.is_admin else "user",
        "website_limit": user.website_limit,
        "database_limit": getattr(user, "database_limit", None),
        "storage_limit_mb": user.storage_limit_mb,
        "token": {"name": ctx.token.name, "can_write": bool(ctx.token.can_write),
                  "expires_at": _iso(ctx.token.expires_at)},
    }


@_tool("list_websites", "List websites",
       "Websites on this account (an administrator sees every account's, optionally filtered by owner).",
       {"owner": {"type": "string", "description": "Administrators only: an account's username"}})
def _list_websites(ctx: Context, args: dict):
    query = ctx.db.query(Website)
    if not ctx.is_admin:
        query = query.filter(Website.owner_id == ctx.user.id)
    elif args.get("owner"):
        query = query.filter(Website.owner_id == _account(ctx, args["owner"]).id)
    owners = {u.id: u.username for u in ctx.db.query(User).all()}
    return [_website_summary(ctx, w, owners) for w in query.order_by(Website.domain).all()]


@_tool("get_website", "Website details",
       "One website: runtime, document root, aliases, databases and its certificate's expiry.",
       {"domain": DOMAIN_ARG}, required=("domain",))
def _get_website(ctx: Context, args: dict):
    from app.services import ssl as ssl_service

    website = _website(ctx, args["domain"])
    owner = ctx.db.query(User).filter(User.id == website.owner_id).first()
    detail = _website_summary(ctx, website, {website.owner_id: owner.username if owner else ""})
    detail.update({
        "root_path": website.root_path,
        "document_root": website.document_root,
        "linux_user": website.linux_user,
        "ssl_wildcard": bool(getattr(website, "ssl_wildcard", False)),
        "ssl_reuse_name": getattr(website, "ssl_reuse_name", None),
        "aliases": [a.domain for a in ctx.db.query(WebsiteAlias).filter(WebsiteAlias.website_id == website.id)],
        "databases": [d.db_name for d in ctx.db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id)],
        "certificate_expires": None,
    })
    try:
        material = ssl_service.read_site_certificate(
            website.domain, website.ssl_mode, website.ssl_cert_path, website.ssl_key_path,
            website.ssl_ca_path, getattr(website, "ssl_reuse_name", None),
        )
        if material:
            detail["certificate_expires"] = _iso(ssl_service.certificate_expiry(material["certificate"]))
    except Exception:  # noqa: BLE001 - an unreadable certificate is not a failed lookup
        pass
    return detail


@_tool("list_databases", "List databases",
       "MariaDB databases on this account, with the website each belongs to. Passwords are never returned.",
       {"owner": {"type": "string", "description": "Administrators only: an account's username"}})
def _list_databases(ctx: Context, args: dict):
    query = ctx.db.query(DatabaseAccount)
    if not ctx.is_admin:
        query = query.filter(DatabaseAccount.owner_id == ctx.user.id)
    elif args.get("owner"):
        query = query.filter(DatabaseAccount.owner_id == _account(ctx, args["owner"]).id)
    domains = {w.id: w.domain for w in ctx.db.query(Website).all()}
    owners = {u.id: u.username for u in ctx.db.query(User).all()}
    return [{
        "db_name": item.db_name,
        "db_user": item.db_user,
        "website": domains.get(item.website_id),
        "owner": owners.get(item.owner_id, ""),
    } for item in query.order_by(DatabaseAccount.db_name).all()]


@_tool("read_site_log", "Read a site log",
       "The last lines of a website's access or error log.",
       {"domain": DOMAIN_ARG,
        "kind": {"type": "string", "enum": ["access", "error"], "description": "Which log (default error)"},
        "lines": {"type": "integer", "minimum": 1, "maximum": 500, "description": "How many lines (default 100)"}},
       required=("domain",))
def _read_site_log(ctx: Context, args: dict):
    from app.api import websites as websites_api

    website = _website(ctx, args["domain"])
    return websites_api.get_website_log(
        website_id=website.id, kind=args.get("kind") or "error",
        lines=int(args.get("lines") or 100), db=ctx.db, current_user=ctx.user,
    )


@_tool("list_backups", "List backups",
       "Full-account backups kept on this server for an account, newest first.",
       {"username": {"type": "string", "description": "Administrators only: another account"}})
def _list_backups(ctx: Context, args: dict):
    from app.services import backup

    account = _account(ctx, args.get("username"))
    items = []
    for path in backup.list_user_backups(account.username):
        try:
            stat = Path(path).stat()
        except OSError:
            continue
        items.append({"file": Path(path).name, "size_bytes": stat.st_size,
                      "modified": _iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(tzinfo=None))})
    return {"account": account.username, "backups": items}


@_tool("list_backup_jobs", "Recent backup jobs",
       "Backup and restore jobs this account started recently (an administrator sees all), with their status.")
def _list_backup_jobs(ctx: Context, args: dict):
    from app.api import maintenance

    return maintenance._list_backup_jobs(ctx.user)


@_tool("server_resources", "Server resources", "CPU, memory, disk and load of the server.")
def _server_resources(ctx: Context, args: dict):
    from app.services import system

    return system.resource_usage()


# -- administrators ---------------------------------------------------------
@_tool("list_users", "List accounts", "Every panel account with its role, limits and number of websites.",
       admin_only=True)
def _list_users(ctx: Context, args: dict):
    counts: dict[int, int] = {}
    for website in ctx.db.query(Website).all():
        counts[website.owner_id] = counts.get(website.owner_id, 0) + 1
    return [{
        "username": user.username,
        "email": user.email,
        "role": "admin" if is_admin_role(user.role) else "user",
        "active": bool(user.is_active),
        "websites": counts.get(user.id, 0),
        "website_limit": user.website_limit,
        "storage_limit_mb": user.storage_limit_mb,
    } for user in ctx.db.query(User).order_by(User.username).all()]


@_tool("list_services", "Service status",
       "Whether each system service the panel manages (web server, PHP, database, ...) is running.",
       admin_only=True)
def _list_services(ctx: Context, args: dict):
    from app.services import system

    out = []
    for name in system.list_services():
        state = "unknown"
        try:
            result = system.service_action(name, "status")
            for line in (result.stdout or "").splitlines():
                if line.strip().startswith("Active:"):
                    state = line.split(":", 1)[1].strip()
                    break
        except Exception as exc:  # noqa: BLE001
            state = f"error: {exc}"
        out.append({"service": name, "state": state})
    return out


@_tool("list_backup_schedules", "Backup schedules",
       "Scheduled backups with their cron, destination and last result.", admin_only=True)
def _list_backup_schedules(ctx: Context, args: dict):
    return [{
        "id": s.id,
        "schedule": s.schedule,
        "all_users": bool(s.all_users),
        "target_id": s.target_id,
        "retention": s.retention,
        "active": bool(s.is_active),
        "last_run_at": _iso(s.last_run_at),
        "last_status": s.last_status,
        "last_message": (s.last_message or "")[:500],
    } for s in ctx.db.query(BackupSchedule).order_by(BackupSchedule.id).all()]


@_tool("recent_audit_log", "Audit log", "The most recent entries of the panel's audit log.",
       {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "How many (default 30)"}},
       admin_only=True)
def _recent_audit_log(ctx: Context, args: dict):
    names = {u.id: u.username for u in ctx.db.query(User).all()}
    rows = ctx.db.query(AuditLog).order_by(AuditLog.id.desc()).limit(int(args.get("limit") or 30)).all()
    return [{"at": _iso(row.created_at), "user": names.get(row.user_id), "action": row.action,
             "target": row.target, "detail": (row.detail or "")[:200]} for row in rows]


@_tool("panel_update_status", "Panel version",
       "The installed panel version and whether a newer release is waiting.", admin_only=True)
def _panel_update_status(ctx: Context, args: dict):
    from app.services import updates

    status = updates.panel_release_status()
    return {key: status.get(key) for key in (
        "current_version", "latest_version", "update_available", "last_checked_at", "check_error")}


# -- actions ----------------------------------------------------------------
@_tool("create_backup", "Run a backup",
       "Start a full backup of an account (files, databases, certificates). Runs in the background; "
       "check list_backup_jobs for its progress.",
       {"username": {"type": "string", "description": "Administrators only: back up another account"}},
       writes=True)
def _create_backup(ctx: Context, args: dict):
    from app.api import maintenance
    from app.schemas.schemas import UserBackupCreate

    account = _account(ctx, args.get("username"))
    return maintenance.create_user_backup(
        payload=UserBackupCreate(user_id=account.id), request=None, db=ctx.db, current_user=ctx.user,
    )


@_tool("issue_ssl_certificate", "Issue a certificate",
       "Request a Let's Encrypt certificate for a website and switch it to HTTPS. The domain must "
       "already point at this server.",
       {"domain": DOMAIN_ARG}, required=("domain",), writes=True)
def _issue_ssl_certificate(ctx: Context, args: dict):
    from app.api import websites as websites_api

    website = _website(ctx, args["domain"])
    updated = websites_api.enable_ssl(website_id=website.id, db=ctx.db, current_user=ctx.user)
    return {"domain": updated.domain, "ssl_enabled": bool(updated.ssl_enabled), "ssl_mode": updated.ssl_mode}


@_tool("set_website_waf", "Switch the WAF",
       "Turn a website's web application firewall on or off.",
       {"domain": DOMAIN_ARG, "enabled": {"type": "boolean", "description": "true to turn it on"}},
       required=("domain", "enabled"), writes=True)
def _set_website_waf(ctx: Context, args: dict):
    from app.api import websites as websites_api
    from app.schemas.schemas import WebsiteWafUpdate

    website = _website(ctx, args["domain"])
    updated = websites_api.set_website_waf(
        website_id=website.id, payload=WebsiteWafUpdate(waf_enabled=bool(args["enabled"])),
        request=None, db=ctx.db, current_user=ctx.user,
    )
    return {"domain": updated.domain, "waf_enabled": bool(updated.waf_enabled)}


@_tool("run_backup_schedule", "Run a backup schedule now",
       "Run a scheduled backup immediately, on the same rotation and retention as its timer.",
       {"schedule_id": {"type": "integer", "description": "From list_backup_schedules"}},
       required=("schedule_id",), admin_only=True, writes=True)
def _run_backup_schedule(ctx: Context, args: dict):
    from app.api import maintenance

    return maintenance.run_backup_schedule_now(
        schedule_id=int(args["schedule_id"]), request=None, db=ctx.db, current_user=ctx.user,
    )


@_tool("restart_service", "Restart a service",
       "Restart or reload a system service the panel manages. Stopping is deliberately not offered.",
       {"service": {"type": "string", "description": "A name from list_services"},
        "action": {"type": "string", "enum": ["restart", "reload"], "description": "Default restart"}},
       required=("service",), admin_only=True, writes=True)
def _restart_service(ctx: Context, args: dict):
    from app.services import system

    action = args.get("action") or "restart"
    if action not in ("restart", "reload"):
        raise ToolError("action must be restart or reload")
    try:
        result = system.service_action(args["service"], action)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if result.returncode != 0:
        raise ToolError((result.stderr or result.stdout or f"{action} failed").strip()[:1000])
    return {"service": args["service"], "action": action, "ok": True}


def visible_tools(ctx: Context) -> list[Tool]:
    return [tool for tool in _TOOLS.values() if tool.visible_to(ctx)]


def _check_arguments(tool: Tool, args: Any) -> dict:
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    unknown = set(args) - set(tool.properties)
    if unknown:
        raise ToolError(f"Unknown argument(s): {', '.join(sorted(unknown))}")
    for name in tool.required:
        if args.get(name) in (None, ""):
            raise ToolError(f"Missing required argument: {name}")
    for name, value in args.items():
        spec = tool.properties[name]
        kind = spec.get("type")
        if kind == "string" and not isinstance(value, str):
            raise ToolError(f"{name} must be a string")
        if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ToolError(f"{name} must be an integer")
        if kind == "boolean" and not isinstance(value, bool):
            raise ToolError(f"{name} must be true or false")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"{name} must be one of: {', '.join(spec['enum'])}")
        if kind == "integer":
            if "minimum" in spec and value < spec["minimum"]:
                raise ToolError(f"{name} must be at least {spec['minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                raise ToolError(f"{name} must be at most {spec['maximum']}")
    return args


def _result(data: Any, is_error: bool = False) -> dict:
    text = data if isinstance(data, str) else json.dumps(data, indent=2, default=str, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call_tool(ctx: Context, name: str, args: Any) -> dict:
    tool = _TOOLS.get(name)
    # A tool this token may not use is reported exactly like one that does not
    # exist, so the list of tools is not something a token can probe.
    if tool is None or not tool.visible_to(ctx):
        raise LookupError(f"Unknown tool: {name}")
    try:
        args = _check_arguments(tool, args)
        data = tool.handler(ctx, args)
    except ToolError as exc:
        return _result(str(exc), is_error=True)
    except HTTPException as exc:
        return _result(str(exc.detail), is_error=True)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        return _result(str(exc), is_error=True)
    except Exception:  # noqa: BLE001 - the model gets a message, the log gets the trace
        logger.exception("MCP tool %s failed for %s", name, ctx.user.username)
        return _result(f"{name} failed with an internal error", is_error=True)
    finally:
        if tool.writes:
            try:
                log_action(ctx.db, ctx.user.id, "mcp_tool", name,
                           json.dumps(args if isinstance(args, dict) else {}, default=str)[:500])
            except Exception:  # noqa: BLE001 - auditing must not turn a result into an error
                ctx.db.rollback()
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    return _result(data)


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700, -32600, -32601, -32602, -32603)


def _error(message_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def negotiate_version(requested: Any) -> str:
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]


def handle_message(ctx: Context, message: Any) -> Optional[dict]:
    """Answer one JSON-RPC message; None for a notification or a response."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message")
    method = message.get("method")
    message_id = message.get("id")
    if method is None:
        # A response to something we asked -- we never ask, so nothing to do.
        return None
    if "id" not in message:
        return None  # notifications/initialized and friends
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(message_id, INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": message_id, "result": {
            "protocolVersion": negotiate_version(params.get("protocolVersion")),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "opanel", "title": "OPanel", "version": APP_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id,
                "result": {"tools": [tool.describe() for tool in visible_tools(ctx)]}}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _error(message_id, INVALID_PARAMS, "tools/call needs a tool name")
        try:
            result = call_tool(ctx, name, params.get("arguments"))
        except LookupError as exc:
            return _error(message_id, INVALID_PARAMS, str(exc))
        return {"jsonrpc": "2.0", "id": message_id, "result": result}
    return _error(message_id, METHOD_NOT_FOUND, f"Method not found: {method}")
