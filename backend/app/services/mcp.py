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
- Every action is written to the audit log. The one that destroys data,
  delete_file, is marked destructive so clients ask before running it; the
  rest -- backups, certificates, the WAF, blocking and unblocking, writing a
  site's files -- can be undone from the panel.
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
    "offered to a token created with actions allowed. File paths are relative "
    "to the website's folder (public_html/ is the web root); read a file before "
    "rewriting it, and ask the user before delete_file. "
    "To look into traffic, start with traffic_summary and drill into "
    "read_waf_access_log. When something needs stopping, prefer add_waf_rule on "
    "the one website involved; block_ip drops the address for the whole server."
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
    # Where the MCP client is calling from, so block_ip can refuse to cut off
    # the very client asking.
    client_ip: str = ""

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
    # Removes something that cannot be put back from the panel: clients show
    # the user a confirmation for these.
    destructive: bool = False

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
                "destructiveHint": self.destructive,
                "idempotentHint": not self.writes,
                "openWorldHint": False,
            },
        }


_TOOLS: dict[str, Tool] = {}


def _tool(name: str, title: str, description: str, properties: Optional[dict] = None,
          required: tuple = (), admin_only: bool = False, writes: bool = False,
          destructive: bool = False):
    def register(fn):
        _TOOLS[name] = Tool(name, title, description, properties or {}, required, fn, admin_only, writes,
                            destructive)
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


# -- traffic ----------------------------------------------------------------
@_tool("read_waf_access_log", "Read the access log",
       "Recent requests to your websites as the web server logged them: time, client IP, method, path, "
       "status, user agent, and whether the WAF blocked it. Filter by site, verdict or a search term.",
       {"domain": {"type": "string", "description": "One website; omit for all of yours"},
        "verdict": {"type": "string", "enum": ["allow", "block"], "description": "Only allowed or only blocked"},
        "search": {"type": "string", "description": "Match against IP, path, user agent, status, reason"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Entries to return (default 50)"},
        "lines": {"type": "integer", "minimum": 100, "maximum": 20000,
                  "description": "How far back to read each log, in lines (default 5000)"}})
def _read_waf_access_log(ctx: Context, args: dict):
    from app.api import waf as waf_api

    report = waf_api.get_waf_access_logs(
        domain=(args.get("domain") or "").strip().lower(), verdict=args.get("verdict") or "",
        q=args.get("search") or "", limit=int(args.get("limit") or 50), offset=0,
        lines=int(args.get("lines") or 5000), db=ctx.db, current_user=ctx.user,
    )
    keep = ("domain", "time", "ip", "method", "path", "status", "verdict", "reason", "user_agent", "referer")
    return {"total_matching": report["total"], "lines_read_per_log": report["lines"],
            "entries": [{key: entry.get(key) for key in keep} for entry in report["entries"]]}


def _top(counter: dict, n: int) -> list:
    return sorted(counter.items(), key=lambda item: item[1], reverse=True)[:n]


@_tool("traffic_summary", "Traffic summary",
       "Aggregate the recent access log of your websites: requests and blocks, status codes, and the "
       "busiest client IPs, paths and user agents. Use this to spot scanners, brute-force attempts and bad bots.",
       {"domain": {"type": "string", "description": "One website; omit for all of yours"},
        "lines": {"type": "integer", "minimum": 100, "maximum": 20000,
                  "description": "How far back to read each log, in lines (default 5000)"},
        "top": {"type": "integer", "minimum": 3, "maximum": 50, "description": "Entries per top list (default 10)"}})
def _traffic_summary(ctx: Context, args: dict):
    from app.api import waf as waf_api
    from app.services import waf

    domain = (args.get("domain") or "").strip().lower()
    readable = waf_api._readable_domains(ctx.db, ctx.user)
    if domain and domain not in readable:
        raise ToolError(f"No website {domain!r} on this account")
    entries, domains, _ = waf.access_log_entries(readable, domain, "", "", int(args.get("lines") or 5000))
    top = int(args.get("top") or 10)
    statuses: dict[str, int] = {}
    per_domain: dict[str, int] = {}
    ips: dict[str, dict] = {}
    paths: dict[str, int] = {}
    agents: dict[str, int] = {}
    blocked = 0
    for entry in entries:
        status = int(entry.get("status") or 0)
        status_class = f"{status // 100}xx" if status else "other"
        statuses[status_class] = statuses.get(status_class, 0) + 1
        per_domain[entry["domain"]] = per_domain.get(entry["domain"], 0) + 1
        is_block = entry.get("verdict") == "block"
        blocked += is_block
        ip = entry.get("ip") or "?"
        slot = ips.setdefault(ip, {"requests": 0, "blocked": 0, "errors_4xx": 0, "paths": {}, "last_seen": ""})
        slot["requests"] += 1
        slot["blocked"] += is_block
        slot["errors_4xx"] += 400 <= status < 500
        path = (entry.get("path") or "").split("?", 1)[0][:200]
        slot["paths"][path] = slot["paths"].get(path, 0) + 1
        slot["last_seen"] = max(slot["last_seen"], entry.get("timestamp") or "")
        paths[path] = paths.get(path, 0) + 1
        agent = (entry.get("user_agent") or "-")[:160]
        agents[agent] = agents.get(agent, 0) + 1
    timestamps = [entry.get("timestamp") for entry in entries if entry.get("timestamp")]
    return {
        "domains": domains,
        "window": {"from": min(timestamps) if timestamps else None, "to": max(timestamps) if timestamps else None},
        "requests": len(entries),
        "blocked": blocked,
        "status_classes": statuses,
        "requests_per_domain": dict(_top(per_domain, 50)),
        "top_ips": [{"ip": ip, **{k: v for k, v in data.items() if k != "paths"},
                     "top_paths": [p for p, _ in _top(data["paths"], 3)]}
                    for ip, data in sorted(ips.items(), key=lambda item: item[1]["requests"], reverse=True)[:top]],
        "top_paths": [{"path": p, "requests": n} for p, n in _top(paths, top)],
        "top_user_agents": [{"user_agent": a, "requests": n} for a, n in _top(agents, top)],
    }


@_tool("list_firewall_rules", "Firewall rules",
       "The panel firewall's rules: open ports, allowed and blocked addresses.", admin_only=True)
def _list_firewall_rules(ctx: Context, args: dict):
    from app.services import firewall

    return {"enabled": firewall.is_enabled(), "rules": firewall.list_rules()}


@_tool("list_waf_rules", "Custom WAF rules",
       "The server-wide custom WAF rules and, for a website, its own custom rules. Rules added through "
       "MCP are marked with '# opanel-mcp:'.",
       {"domain": {"type": "string", "description": "Also show this website's custom rules"}}, admin_only=True)
def _list_waf_rules(ctx: Context, args: dict):
    from app.services import waf

    out = {"server_wide": (waf.custom_rules().stdout or "").strip()}
    if args.get("domain"):
        website = _website(ctx, args["domain"])
        out["site"] = {"domain": website.domain, "waf_enabled": bool(website.waf_enabled),
                       "custom_rules": waf.website_custom_rules(website)}
    return out


def _server_addresses() -> set[str]:
    from app.services.shell import shell

    try:
        result = shell.run(["hostname", "-I"], check=False)
    except Exception:  # noqa: BLE001
        return set()
    return set((result.stdout or "").split())


def _refuse_block(ctx: Context, network: str):
    """Why this network must not be blocked, or None.

    Blocking is the one action here that can lock people out of the whole box,
    so the tool refuses what no traffic analysis should ever conclude: the
    machine itself, the client asking, special-purpose ranges, and networks so
    wide they would take out a country's worth of visitors at once.
    """
    import ipaddress

    try:
        net = ipaddress.ip_network(network, strict=False)
    except ValueError:
        return "ip must be an IPv4/IPv6 address or CIDR network"
    if (net.version == 4 and net.prefixlen < 16) or (net.version == 6 and net.prefixlen < 32):
        return "network is too wide to block (at most a /16 for IPv4, /32 for IPv6)"
    if net.is_loopback or net.is_unspecified or net.is_link_local or net.is_multicast or net.is_private \
            or net.is_reserved:
        return "that is a private or special-purpose range, not an internet client"
    for protected in _server_addresses() | ({ctx.client_ip} if ctx.client_ip else set()):
        try:
            if ipaddress.ip_address(protected) in net:
                return f"{network} contains {protected}, which is this server or the MCP client itself"
        except ValueError:
            continue
    return None


@_tool("block_ip", "Block an address",
       "Drop all traffic from an IP address or network at the server firewall. Refused for this server's "
       "own addresses, the MCP client's address, private ranges and networks wider than /16. If a site sits "
       "behind a CDN or proxy, the address in its log may be the proxy's: blocking that cuts off real visitors.",
       {"ip": {"type": "string", "description": "An address, e.g. 203.0.113.7, or a network, e.g. 203.0.113.0/24"},
        "reason": {"type": "string", "description": "Why, for the audit log"}},
       required=("ip",), admin_only=True, writes=True)
def _block_ip(ctx: Context, args: dict):
    import ipaddress

    from app.services import firewall

    reason = _refuse_block(ctx, args["ip"])
    if reason:
        raise ToolError(f"Not blocked: {reason}")
    network = str(ipaddress.ip_network(args["ip"].strip(), strict=False))
    for rule in firewall.list_rules():
        if rule.get("action") == "deny" and rule.get("type") == "ip" and rule.get("network") == network \
                and not rule.get("port"):
            return {"ip": network, "blocked": True, "already": True, "rule_id": rule.get("id")}
    firewall.block_ip(network)
    return {"ip": network, "blocked": True, "already": False}


@_tool("unblock_ip", "Unblock an address",
       "Remove the firewall block the panel holds for an IP address or network, as added by block_ip or on "
       "the Firewall page. Blocklists and Fail2ban bans are separate and not touched.",
       {"ip": {"type": "string", "description": "The address or network exactly as it was blocked"}},
       required=("ip",), admin_only=True, writes=True)
def _unblock_ip(ctx: Context, args: dict):
    import ipaddress

    from app.services import firewall

    try:
        network = str(ipaddress.ip_network(args["ip"].strip(), strict=False))
    except ValueError as exc:
        raise ToolError("ip must be an IPv4/IPv6 address or CIDR network") from exc
    removed = []
    for rule in firewall.list_rules():
        if rule.get("action") == "deny" and rule.get("type") == "ip" and rule.get("network") == network:
            firewall.delete_rule(int(rule["id"]))
            removed.append(rule["id"])
    if not removed:
        raise ToolError(f"{network} is not blocked by a panel firewall rule (a blocklist or Fail2ban ban "
                        "is managed on its own page)")
    return {"ip": network, "unblocked": True, "removed_rule_ids": removed}


@_tool("add_waf_rule", "Add a WAF rule",
       "Make the WAF answer 403 to requests matching a client IP, a path prefix, a user-agent substring or a "
       "query-string substring, on one website or server-wide. The rule is generated by the panel; raw "
       "ModSecurity is not accepted.",
       {"match": {"type": "string", "enum": ["ip", "path", "user_agent", "query"], "description": "What to match"},
        "value": {"type": "string", "description": "IP/CIDR, a path starting with /, or a substring (3-120 chars)"},
        "domain": {"type": "string", "description": "One website; omit to apply server-wide"},
        "note": {"type": "string", "description": "Why, kept next to the rule (80 chars)"}},
       required=("match", "value"), admin_only=True, writes=True)
def _add_waf_rule(ctx: Context, args: dict):
    from app.api import waf as waf_api
    from app.services import waf

    match, value = args["match"], args["value"]
    signature = waf.mcp_rule_signature(match, value)
    server_wide = (waf.custom_rules().stdout or "").strip()
    site_texts = [w.waf_custom_rules or "" for w in ctx.db.query(Website).all()]
    website = _website(ctx, args["domain"]) if args.get("domain") else None
    current = waf.website_custom_rules(website) if website else server_wide
    if signature in current:
        return {"scope": website.domain if website else "server-wide", "already": True, "rule": signature}
    rule_id = waf.next_mcp_rule_id([server_wide, *site_texts])
    rule = waf.render_mcp_rule(match, value, rule_id, args.get("note") or "", ctx.user.username)
    updated = f"{current}\n\n{rule}".strip()
    if website:
        waf_api.save_website_waf(
            payload=waf_api.WebsiteWafRulesUpdate(
                enabled_rule_ids=sorted(waf.website_enabled_rule_ids(website)), custom_rules=updated),
            website_id=website.id, db=ctx.db, current_user=ctx.user,
        )
    else:
        waf_api.save_waf_custom_rules(payload=waf_api.WafCustomRulesUpdate(content=updated), current_user=ctx.user)
    out = {"scope": website.domain if website else "server-wide", "already": False, "rule_id": rule_id, "rule": rule}
    if website and not website.waf_enabled:
        out["warning"] = "The WAF is off for this website, so the rule has no effect until it is turned on."
    return out


# -- files ------------------------------------------------------------------
# Through the file manager's own endpoints: paths are confined to the site's
# folder with symlinks refused, writes land as the site's Linux user, and the
# account's storage quota applies -- the same rules as the panel's editor.
PATH_ARG = {"type": "string", "description": "Path inside the website's folder, e.g. public_html/index.php"}
READ_LINE_LIMIT = 2000
SEARCH_SKIP_DIRS = {".git", "node_modules", ".cache", "cache", "uploads"}
SEARCH_MAX_FILES = 20000
SEARCH_MAX_FILE_BYTES = 512 * 1024
SEARCH_MAX_MATCHES = 100
SEARCH_MAX_TOTAL_BYTES = 200 * 1024 * 1024


def _clean_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip().strip("/")


@_tool("list_files", "List files",
       "The files and folders in a directory of a website. The web root is public_html.",
       {"domain": DOMAIN_ARG,
        "path": {"type": "string", "description": "Directory inside the website's folder (default public_html)"}},
       required=("domain",))
def _list_files(ctx: Context, args: dict):
    from app.api import maintenance

    website = _website(ctx, args["domain"])
    path = _clean_path(args.get("path") if args.get("path") is not None else "public_html")
    items = maintenance.list_files(website_id=website.id, path=path, db=ctx.db, current_user=ctx.user)["items"]
    return {"path": path or ".", "entries": [{
        "name": item["name"], "path": item["path"], "type": "dir" if item["is_dir"] else "file",
        "size": item["size"], "modified": _iso(datetime.fromtimestamp(item["modified"], timezone.utc).replace(tzinfo=None)),
    } for item in items]}


@_tool("read_file", "Read a file",
       f"A text file of a website, a range of lines at a time (up to {READ_LINE_LIMIT}); total_lines tells you "
       "whether there is more.",
       {"domain": DOMAIN_ARG, "path": PATH_ARG,
        "start_line": {"type": "integer", "minimum": 1, "description": "First line, 1-based (default 1)"},
        "line_count": {"type": "integer", "minimum": 1, "maximum": READ_LINE_LIMIT,
                       "description": f"Lines to return (default {READ_LINE_LIMIT})"}},
       required=("domain", "path"))
def _read_file(ctx: Context, args: dict):
    from app.api import maintenance

    website = _website(ctx, args["domain"])
    path = _clean_path(args["path"])
    content = maintenance.read_file(website_id=website.id, path=path, db=ctx.db, current_user=ctx.user)["content"]
    lines = content.splitlines(keepends=True)
    start = int(args.get("start_line") or 1)
    count = int(args.get("line_count") or READ_LINE_LIMIT)
    chunk = lines[start - 1:start - 1 + count]
    return {"path": path, "total_lines": len(lines), "start_line": start,
            "end_line": start - 1 + len(chunk), "content": "".join(chunk)}


@_tool("search_files", "Search in files",
       "Find a piece of text in a website's files and return the matching lines. Skips .git, node_modules, "
       "cache and uploads folders, binary files and files over 512 KB.",
       {"domain": DOMAIN_ARG,
        "text": {"type": "string", "description": "The text to look for (plain text, not a regex)"},
        "path": {"type": "string", "description": "Folder to search (default public_html)"},
        "file_suffix": {"type": "string", "description": "Only files ending with this, e.g. .php"},
        "case_sensitive": {"type": "boolean", "description": "Default false"}},
       required=("domain", "text"))
def _search_files(ctx: Context, args: dict):
    import os

    from app.services import file_manager

    website = _website(ctx, args["domain"])
    needle = args["text"]
    if len(needle) < 2:
        raise ToolError("text must be at least 2 characters")
    folded = needle if args.get("case_sensitive") else needle.lower()
    base = file_manager._safe_path(website, _clean_path(args.get("path") or "public_html"))
    if not base.is_dir():
        raise ToolError("path is not a folder")
    root = Path(website.root_path).resolve()
    suffix = (args.get("file_suffix") or "").lower()
    matches, scanned, read_bytes, truncated = [], 0, 0, False
    for folder, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SEARCH_SKIP_DIRS and not os.path.islink(os.path.join(folder, d))]
        for name in files:
            if suffix and not name.lower().endswith(suffix):
                continue
            full = Path(folder) / name
            try:
                if full.is_symlink() or full.stat().st_size > SEARCH_MAX_FILE_BYTES:
                    continue
                data = full.read_bytes()
            except OSError:
                continue
            scanned += 1
            read_bytes += len(data)
            if b"\x00" in data[:1024]:
                continue
            text = data.decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                if folded in (line if args.get("case_sensitive") else line.lower()):
                    matches.append({"path": full.relative_to(root).as_posix(), "line": number,
                                    "text": line.strip()[:200]})
                    if len(matches) >= SEARCH_MAX_MATCHES:
                        truncated = True
                        break
            if truncated or scanned >= SEARCH_MAX_FILES or read_bytes >= SEARCH_MAX_TOTAL_BYTES:
                truncated = True
                break
        if truncated:
            break
    return {"files_scanned": scanned, "truncated": truncated, "matches": matches}


@_tool("write_file", "Write a file",
       "Create a file or replace its whole content (folders on the way are created). Read the current file "
       "first when changing one: this overwrites it.",
       {"domain": DOMAIN_ARG, "path": PATH_ARG, "content": {"type": "string", "description": "The full new content"}},
       required=("domain", "path"), writes=True)
def _write_file(ctx: Context, args: dict):
    from app.api import maintenance

    from app.services import file_manager

    website = _website(ctx, args["domain"])
    path = _clean_path(args["path"])
    if not path:
        raise ToolError("path must name a file")
    content = args.get("content") or ""
    # Missing folders on the way are made through the file manager too, so they
    # belong to the site user like the file; the root helper would make them
    # anyway, a site with no Linux user of its own would not.
    parts = path.split("/")[:-1]
    for depth in range(len(parts)):
        folder = "/".join(parts[:depth + 1])
        if not file_manager._safe_path(website, folder).exists():
            maintenance.make_directory(
                payload=maintenance.FileMkdir(website_id=website.id, path="/".join(parts[:depth]), name=parts[depth]),
                db=ctx.db, current_user=ctx.user,
            )
    target = maintenance.write_file(
        payload=maintenance.FileWrite(website_id=website.id, path=path, content=content),
        db=ctx.db, current_user=ctx.user,
    )["target"]
    return {"path": path, "written": True, "bytes": len(content.encode("utf-8")), "target": target}


@_tool("create_directory", "Create a folder", "Create a folder inside a website.",
       {"domain": DOMAIN_ARG, "path": {"type": "string", "description": "The new folder, e.g. public_html/assets/js"}},
       required=("domain", "path"), writes=True)
def _create_directory(ctx: Context, args: dict):
    from app.api import maintenance

    website = _website(ctx, args["domain"])
    parent, _, name = _clean_path(args["path"]).rpartition("/")
    if not name:
        raise ToolError("path must name a folder")
    target = maintenance.make_directory(
        payload=maintenance.FileMkdir(website_id=website.id, path=parent, name=name), db=ctx.db, current_user=ctx.user,
    )["target"]
    return {"path": _clean_path(args["path"]), "created": True, "target": target}


@_tool("move_file", "Move or rename",
       "Move or rename a file or folder inside a website.",
       {"domain": DOMAIN_ARG, "path": PATH_ARG,
        "new_path": {"type": "string", "description": "Where it should end up, e.g. public_html/old/index.php"}},
       required=("domain", "path", "new_path"), writes=True)
def _move_file(ctx: Context, args: dict):
    from app.api import maintenance

    website = _website(ctx, args["domain"])
    source, dest = _clean_path(args["path"]), _clean_path(args["new_path"])
    if not source or not dest:
        raise ToolError("path and new_path must both name something inside the website")
    src_parent, _, src_name = source.rpartition("/")
    dst_parent, _, dst_name = dest.rpartition("/")
    current = source
    if dst_parent != src_parent:
        maintenance.move_entries(
            payload=maintenance.FileTransfer(website_id=website.id, paths=[source], destination_path=dst_parent),
            db=ctx.db, current_user=ctx.user,
        )
        current = f"{dst_parent}/{src_name}" if dst_parent else src_name
    if dst_name != src_name:
        maintenance.rename_entry(
            payload=maintenance.FileRename(website_id=website.id, path=current, new_name=dst_name),
            db=ctx.db, current_user=ctx.user,
        )
    return {"from": source, "to": dest, "moved": True}


@_tool("delete_file", "Delete a file or folder",
       "Delete a file, or a folder with everything in it, from a website. This cannot be undone from the "
       "panel: ask the user first, and make a backup (create_backup) before deleting anything large.",
       {"domain": DOMAIN_ARG, "path": PATH_ARG}, required=("domain", "path"), writes=True, destructive=True)
def _delete_file(ctx: Context, args: dict):
    from app.api import maintenance

    website = _website(ctx, args["domain"])
    path = _clean_path(args["path"])
    # The website folder and its web root hold everything; losing either is
    # losing the site, which is never what a one-line tool call means.
    if path in ("", ".", website.document_root or "public_html"):
        raise ToolError("Refusing to delete the website folder or its web root")
    deleted = maintenance.delete_entries(
        payload=maintenance.FileBulkDelete(website_id=website.id, paths=[path]), db=ctx.db, current_user=ctx.user,
    )["deleted"]
    return {"path": path, "deleted": True, "targets": deleted}


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
                # A file's content can be a wp-config.php: the log records its
                # size, not the text.
                logged = {key: (f"<{len(value)} characters>" if key == "content" and isinstance(value, str) else value)
                          for key, value in (args.items() if isinstance(args, dict) else ())}
                log_action(ctx.db, ctx.user.id, "mcp_tool", name, json.dumps(logged, default=str)[:500])
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
