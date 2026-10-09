"""Git repositories in a hosting account's home (operator, 2026-10-09).

An account keeps repositories anywhere in /home/<user>, as cPanel's Git
Version Control does: clone one from GitHub/GitLab, or start one in a folder
that already has files. From the panel it can pull (deploy), commit and push
what changed on the server, switch branch, and run a few preset commands
after a deploy (composer, npm, artisan, WP-CLI). A push to the remote can
deploy by itself through a webhook.

Every git command runs as the account's own Linux user, through
opanel-helper's git-repo: a repository's config can name programs git runs,
so git never runs as root or as opanel on a tree its owner controls. A
private remote is reached with a deploy key the panel generates or an HTTPS
token; both are stored encrypted here and handed to the helper on stdin.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.secrets import decrypt, encrypt
from app.models.entities import GitOperation, GitRepository, User
from app.services import site_users
from app.services.shell import shell

logger = logging.getLogger("opanel.git")

HOME_ROOT = PurePosixPath("/home")
MAX_REPOS_PER_OWNER = 20
KEEP_OPERATIONS = 30
LOG_LIMIT = 64_000
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,99}$")
HTTPS_URL_RE = re.compile(r"^https://[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]{1,5})?/[A-Za-z0-9._~/+%-]+$")
SSH_URL_RE = re.compile(r"^ssh://([A-Za-z0-9._-]+@)?[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]{1,5})?/[A-Za-z0-9._~/+%-]+$")
SCP_URL_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9][A-Za-z0-9.-]*:[A-Za-z0-9._~+%][A-Za-z0-9._~/+%-]*$")
HTTPS_USER_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,100}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
AUTH_TYPES = ("none", "ssh", "https")
PHP_VERSIONS = ("7.4", "8.1", "8.2", "8.3", "8.4", "8.5")
# What may run after a deploy, in the order the account picks. The helper
# holds the actual command lines; nothing an account types is ever run.
PRESETS = {
    "composer-install": "composer install --no-dev --optimize-autoloader",
    "npm-ci": "npm ci",
    "npm-build": "npm run build",
    "artisan-migrate": "php artisan migrate --force",
    "artisan-optimize": "php artisan optimize",
    "wp-cache-flush": "wp cache flush",
}
RESULT_MARK = "OPANEL_GIT_RESULT "
STATUS_MARK = "OPANEL_GIT_STATUS "

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="opanel-git")
_busy: set[int] = set()
_busy_lock = threading.Lock()
# Tests point the background work at their own database.
_session_factory = None


# --- Validation ----------------------------------------------------------------------

def linux_user_for(owner: User) -> str:
    return site_users.linux_user_for_panel_username(owner.username)


def home_for(owner: User) -> PurePosixPath:
    return HOME_ROOT / linux_user_for(owner)


def validate_name(value: str) -> str:
    name = (value or "").strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError("Name must be 1-64 letters, digits, spaces, dots, dashes or underscores")
    return name


def validate_branch(value: str) -> str:
    branch = (value or "").strip()
    if (not BRANCH_RE.fullmatch(branch) or ".." in branch or "//" in branch
            or branch.endswith(("/", ".lock")) or "/." in branch or "@{" in branch):
        raise ValueError("Invalid branch name")
    return branch


def validate_url(value: str, required: bool = True) -> str:
    url = (value or "").strip()
    if not url:
        if required:
            raise ValueError("Enter the repository URL")
        return ""
    if re.match(r"^https?://[^/]*@", url):
        raise ValueError("Put the token in the HTTPS token field, not in the URL")
    if len(url) > 500 or not (HTTPS_URL_RE.fullmatch(url) or SSH_URL_RE.fullmatch(url) or SCP_URL_RE.fullmatch(url)):
        raise ValueError("Use an https:// URL, an ssh:// URL or git@host:owner/repo.git")
    return url


def url_is_ssh(url: str) -> bool:
    return bool(SSH_URL_RE.fullmatch(url or "") or SCP_URL_RE.fullmatch(url or ""))


def resolve_path(owner: User, relative: str) -> str:
    """An absolute repository path from one relative to the account's home."""
    value = (relative or "").strip().strip("/")
    parts = [part for part in value.split("/") if part]
    if not parts:
        raise ValueError("Choose a folder inside the account's home")
    if len(parts) > 8:
        raise ValueError("The folder is too deep")
    for part in parts:
        if not SEGMENT_RE.fullmatch(part):
            raise ValueError(f"Invalid folder name: {part} (letters, digits, dots, dashes; not starting with a dot)")
    return str(home_for(owner).joinpath(*parts))


def relative_path(repo: GitRepository, owner: User) -> str:
    home = str(home_for(owner))
    return repo.path[len(home) + 1:] if repo.path.startswith(home + "/") else repo.path


def check_overlap(db: Session, path: str, exclude_id: Optional[int] = None) -> None:
    """One repository per folder, and none inside another."""
    for other_id, other in db.query(GitRepository.id, GitRepository.path).all():
        if other_id == exclude_id:
            continue
        if path == other or path.startswith(other + "/") or other.startswith(path + "/"):
            raise ValueError("That folder is, or is inside, a repository the panel already has")


def validate_presets(values) -> list[str]:
    chosen = []
    for value in values or []:
        if value not in PRESETS:
            raise ValueError(f"Unknown command: {value}")
        if value not in chosen:
            chosen.append(value)
    return chosen


def validate_php(value: str) -> str:
    version = (value or "").strip()
    if not version:
        return ""
    if version not in PHP_VERSIONS:
        raise ValueError("Unsupported PHP version")
    return version


def presets_of(repo: GitRepository) -> list[str]:
    try:
        values = json.loads(repo.deploy_commands or "[]")
    except ValueError:
        return []
    return [value for value in values if value in PRESETS] if isinstance(values, list) else []


# --- Credentials ---------------------------------------------------------------------

def generate_ssh_key(comment: str) -> tuple[str, str]:
    """A new ed25519 deploy key: (private, public)."""
    with tempfile.TemporaryDirectory(prefix="opanel-git-key-") as tmp:
        key = Path(tmp) / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)],
                       check=True, capture_output=True, timeout=30)
        return key.read_text(encoding="utf-8"), (Path(tmp) / "key.pub").read_text(encoding="utf-8").strip()


def set_ssh_key(repo: GitRepository, owner: User) -> None:
    private, public = generate_ssh_key(f"{owner.username}-{repo.name}@opanel".replace(" ", "-"))
    repo.ssh_private_key = encrypt(private)
    repo.ssh_public_key = public


def credentials(repo: GitRepository) -> str:
    """What the helper reads on stdin before talking to the remote."""
    if repo.auth_type == "ssh" and repo.ssh_private_key:
        return "ssh\n" + decrypt(repo.ssh_private_key)
    if repo.auth_type == "https" and repo.https_token:
        return f"https\n{repo.https_username or 'git'}\n{decrypt(repo.https_token)}"
    return "none\n"


def new_webhook_token() -> str:
    return secrets.token_urlsafe(32)


# --- Helper calls --------------------------------------------------------------------

def _helper(action: str, repo: GitRepository, owner: User, args: list[str], *, with_credentials: bool = False):
    return shell.privileged(
        "git-repo",
        helper_args=[action, linux_user_for(owner), repo.path, *args],
        check=False,
        input=credentials(repo) if with_credentials else None,
        sensitive=with_credentials,
    )


def _parse(result) -> tuple[dict, str]:
    """The helper's result line, and its output without the marker lines."""
    found: dict = {}
    lines = []
    for line in (result.stdout or "").splitlines():
        if line.startswith(RESULT_MARK):
            try:
                found = json.loads(line[len(RESULT_MARK):])
            except ValueError:
                pass
            continue
        if line.startswith(STATUS_MARK):
            continue
        lines.append(line)
    error = (result.stderr or "").strip()
    if error:
        lines.append(error)
    if not found:
        found = {"ok": result.returncode == 0}
    return found, "\n".join(lines)


def status(repo: GitRepository, owner: User) -> Optional[dict]:
    """Branch, last commits, local changes. None until the repository exists."""
    if not repo.ready or settings.command_dry_run:
        return None
    result = _helper("status", repo, owner, [])
    for line in (result.stdout or "").splitlines():
        if line.startswith(STATUS_MARK):
            try:
                return json.loads(line[len(STATUS_MARK):])
            except ValueError:
                break
    raise RuntimeError((result.stderr or result.stdout or "Could not read the repository").strip()[-300:])


def set_remote(repo: GitRepository, owner: User, url: str) -> None:
    if not repo.ready or settings.command_dry_run:
        return
    result = _helper("set-remote", repo, owner, [url or "-"])
    found, log = _parse(result)
    if not found.get("ok"):
        raise RuntimeError(log.strip()[-300:] or "Could not change the remote")


# --- Operations ----------------------------------------------------------------------

def is_busy(repo_id: int) -> bool:
    with _busy_lock:
        return repo_id in _busy


def start(db: Session, repo: GitRepository, action: str, actor: Optional[User], trigger: str = "manual",
          **params) -> GitOperation:
    """Queue a clone, init, deploy, push or checkout. One at a time per repository."""
    if action not in ("clone", "init", "deploy", "push", "checkout"):
        raise ValueError("Unknown operation")
    if action not in ("clone", "init") and not repo.ready:
        raise ValueError("The repository has not been cloned yet")
    with _busy_lock:
        if repo.id in _busy:
            raise RuntimeError("Another git operation is running on this repository. Wait for it to finish.")
        _busy.add(repo.id)
    try:
        # Rows left "running" by a restart are not running.
        db.query(GitOperation).filter(GitOperation.repository_id == repo.id,
                                      GitOperation.status == "running").update(
            {"status": "failed", "finished_at": datetime.utcnow()}, synchronize_session=False)
        operation = GitOperation(repository_id=repo.id, action=action, trigger=trigger,
                                 actor_id=actor.id if actor else None, status="running")
        db.add(operation)
        db.commit()
        db.refresh(operation)
        _prune(db, repo.id)
    except Exception:
        with _busy_lock:
            _busy.discard(repo.id)
        raise
    _executor.submit(_execute, operation.id, repo.id, params)
    return operation


def settle_stale(db: Session, repo: GitRepository) -> None:
    """An operation still "running" with nothing running it was cut off by a
    panel restart: say so, rather than show it running for ever."""
    if is_busy(repo.id):
        return
    stale = db.query(GitOperation).filter(GitOperation.repository_id == repo.id,
                                          GitOperation.status == "running").all()
    for operation in stale:
        operation.status = "failed"
        operation.finished_at = datetime.utcnow()
        operation.log = ((operation.log or "") + "\nInterrupted: the panel restarted while this ran.").strip()
    if stale:
        db.commit()


def _prune(db: Session, repo_id: int) -> None:
    keep = [row.id for row in db.query(GitOperation.id).filter(GitOperation.repository_id == repo_id)
            .order_by(GitOperation.id.desc()).limit(KEEP_OPERATIONS).all()]
    if keep:
        db.query(GitOperation).filter(GitOperation.repository_id == repo_id,
                                      GitOperation.id.notin_(keep)).delete(synchronize_session=False)
        db.commit()


def _author(actor: Optional[User], owner: User) -> tuple[str, str]:
    who = actor or owner
    email = (who.email or "").strip()
    if not EMAIL_RE.fullmatch(email):
        email = f"{who.username}@users.opanel.invalid"
    return who.username, email


def _execute(operation_id: int, repo_id: int, params: dict) -> None:
    from app.core.database import SessionLocal

    try:
        with (_session_factory or SessionLocal)() as db:
            operation = db.get(GitOperation, operation_id)
            repo = db.get(GitRepository, operation.repository_id) if operation else None
            if not operation or not repo:
                return
            owner = db.get(User, repo.owner_id)
            actor = db.get(User, operation.actor_id) if operation.actor_id else None
            logs: list[str] = []
            ok, before, after = False, "", ""
            try:
                ok, before, after = _run_steps(repo, owner, actor, operation.action, params, logs)
            except Exception as exc:  # noqa: BLE001 - the log says what went wrong
                logger.warning("git %s failed for repository %s", operation.action, repo.id, exc_info=True)
                logs.append(str(exc))
            if ok and operation.action in ("clone", "init"):
                repo.ready = True
            if ok and operation.action == "checkout":
                repo.branch = params["branch"]
            text = "\n".join(part for part in logs if part).strip()
            operation.log = text[-LOG_LIMIT:]
            operation.status = "done" if ok else "failed"
            operation.commit_before = before[:64]
            operation.commit_after = after[:64]
            operation.finished_at = datetime.utcnow()
            db.commit()
    finally:
        with _busy_lock:
            _busy.discard(repo_id)


def _run_steps(repo: GitRepository, owner: User, actor: Optional[User], action: str, params: dict,
               logs: list[str]) -> tuple[bool, str, str]:
    if settings.command_dry_run:
        logs.append(f"(dry run) git {action}")
        return True, "", ""

    def step(helper_action: str, args: list[str], with_credentials: bool = False) -> dict:
        found, log = _parse(_helper(helper_action, repo, owner, args, with_credentials=with_credentials))
        logs.append(log)
        return found

    if action == "clone":
        found = step("clone", [repo.remote_url, repo.branch], with_credentials=True)
        return bool(found.get("ok")), "", found.get("after", "")
    if action == "init":
        found = step("init", [repo.branch, *([repo.remote_url] if repo.remote_url else [])])
        return bool(found.get("ok")), "", found.get("after", "")
    if action == "checkout":
        found = step("checkout", [params["branch"]], with_credentials=True)
        return bool(found.get("ok")), found.get("before", ""), found.get("after", "")
    if action == "push":
        name, email = _author(actor, owner)
        found = step("push", [repo.branch, name, email, params["message"]], with_credentials=True)
        return bool(found.get("ok")), found.get("before", ""), found.get("after", "")
    # deploy: pull, then the preset commands in order; the first failure stops it.
    found = step("pull", [repo.branch, "reset" if params.get("discard_local") else "ff"], with_credentials=True)
    before, after = found.get("before", ""), found.get("after", "")
    if not found.get("ok"):
        return False, before, after
    if params.get("run_commands", True):
        php = repo.php_version or settings.default_php_version
        for preset in presets_of(repo):
            if not step("run", [preset, php]).get("ok"):
                return False, before, after
    return True, before, after


# --- Webhook -------------------------------------------------------------------------

def webhook_repository(db: Session, repo_id: int, token: str) -> Optional[GitRepository]:
    repo = db.get(GitRepository, repo_id)
    if (not repo or not repo.ready or not repo.webhook_enabled or not repo.webhook_token
            or not hmac.compare_digest(repo.webhook_token.encode(), (token or "").encode())):
        return None
    return repo


def webhook_signature_ok(repo: GitRepository, body: bytes, headers) -> bool:
    """When the sender signs (GitHub with the token as its secret) or sends a
    token header (GitLab), it must match; the URL's token is enough alone."""
    signature = headers.get("x-hub-signature-256")
    if signature:
        expected = "sha256=" + hmac.new(repo.webhook_token.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return False
    gitlab = headers.get("x-gitlab-token")
    if gitlab and not hmac.compare_digest(gitlab.encode(), repo.webhook_token.encode()):
        return False
    return True


def webhook_branch(payload) -> Optional[str]:
    """The branch a push event is about: GitHub, GitLab and Gitea send ref,
    Bitbucket push.changes. None when the payload says neither."""
    if not isinstance(payload, dict):
        return None
    ref = payload.get("ref")
    if isinstance(ref, str) and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    try:
        change = payload["push"]["changes"][-1]["new"]
        if change.get("type") == "branch":
            return change.get("name")
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return None


# --- Rows ----------------------------------------------------------------------------

def delete(db: Session, repo: GitRepository) -> None:
    """The panel forgets the repository; its files stay where they are."""
    db.query(GitOperation).filter(GitOperation.repository_id == repo.id).delete(synchronize_session=False)
    db.delete(repo)


def delete_for_owner(db: Session, owner: User) -> None:
    for repo in db.query(GitRepository).filter(GitRepository.owner_id == owner.id).all():
        delete(db, repo)


def serialize_operation(operation: GitOperation, with_log: bool = False) -> dict:
    data = {
        "id": operation.id,
        "action": operation.action,
        "trigger": operation.trigger,
        "status": operation.status,
        "commit_before": operation.commit_before,
        "commit_after": operation.commit_after,
        "started_at": operation.started_at.isoformat() + "Z" if operation.started_at else "",
        "finished_at": operation.finished_at.isoformat() + "Z" if operation.finished_at else "",
    }
    if with_log:
        data["log"] = operation.log or ""
    return data


def serialize(repo: GitRepository, owner: User, last: Optional[GitOperation] = None, *, hide_secrets: bool = False) -> dict:
    return {
        "id": repo.id,
        "name": repo.name,
        "owner_id": repo.owner_id,
        "owner": owner.username,
        "path": repo.path,
        "relative_path": relative_path(repo, owner),
        "remote_url": repo.remote_url,
        "branch": repo.branch,
        "auth_type": repo.auth_type,
        "https_username": repo.https_username,
        "has_token": bool(repo.https_token),
        "ssh_public_key": repo.ssh_public_key if repo.auth_type == "ssh" else "",
        "deploy_commands": presets_of(repo),
        "php_version": repo.php_version,
        "webhook_enabled": repo.webhook_enabled,
        "webhook_path": (f"/api/git/hook/{repo.id}/{repo.webhook_token}"
                         if repo.webhook_enabled and repo.webhook_token and not hide_secrets else ""),
        "ready": repo.ready,
        "busy": is_busy(repo.id),
        "created_at": repo.created_at.isoformat() + "Z" if repo.created_at else "",
        "last_operation": serialize_operation(last) if last else None,
    }
