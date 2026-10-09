"""Git repositories in an account's home: clone, deploy, push, webhooks."""
from __future__ import annotations

import json
from typing import Literal, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.access import can_access_owner, scope_owner
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import is_admin_role
from app.core.secrets import encrypt
from app.models.entities import GitOperation, GitRepository, User
from app.services import demo_mode, git_repos
from app.services.audit import log_action

router = APIRouter(prefix="/git", tags=["git"])


def _require_enabled() -> None:
    """Git is an addon: stopped or not installed, its routes and webhooks answer
    as if they were not there."""
    if not git_repos.enabled():
        raise HTTPException(status_code=404, detail="The Git addon is not installed")


guarded = APIRouter(dependencies=[Depends(_require_enabled)])

WEBHOOK_BODY_LIMIT = 2 * 1024 * 1024


class RepoCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # Relative to the account's home, e.g. "apps/shop" or "example.com/public_html".
    path: str = Field(min_length=1, max_length=400)
    # clone: from remote_url into an empty or new folder.
    # init: a new repository in a folder that may already have files.
    mode: Literal["clone", "init"] = "clone"
    remote_url: str = Field(default="", max_length=500)
    branch: str = Field(default="main", min_length=1, max_length=100)
    auth_type: Literal["none", "ssh", "https"] = "none"
    https_username: str = Field(default="", max_length=100)
    https_token: str = Field(default="", max_length=4000)
    deploy_commands: list[str] = Field(default_factory=list, max_length=6)
    php_version: str = Field(default="", max_length=8)
    # Administrators create a repository in a hosting account's home.
    owner_id: Optional[int] = None


class RepoUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    remote_url: Optional[str] = Field(default=None, max_length=500)
    auth_type: Optional[Literal["none", "ssh", "https"]] = None
    https_username: Optional[str] = Field(default=None, max_length=100)
    # Empty keeps the stored token.
    https_token: Optional[str] = Field(default=None, max_length=4000)
    deploy_commands: Optional[list[str]] = Field(default=None, max_length=6)
    php_version: Optional[str] = Field(default=None, max_length=8)
    webhook_enabled: Optional[bool] = None


class StartIn(BaseModel):
    mode: Literal["clone", "init"] = "clone"


class DeployIn(BaseModel):
    # Throw away changes made on the server and match the remote exactly.
    discard_local: bool = False
    run_commands: bool = True


class PushIn(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class CheckoutIn(BaseModel):
    branch: str = Field(min_length=1, max_length=100)


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _repo_for(db: Session, repo_id: int, current_user: User) -> tuple[GitRepository, User]:
    repo = db.get(GitRepository, repo_id)
    if not repo or not can_access_owner(db, current_user, repo.owner_id):
        raise HTTPException(status_code=404, detail="Repository not found")
    owner = db.get(User, repo.owner_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo, owner


def _last_operation(db: Session, repo_id: int) -> Optional[GitOperation]:
    return (db.query(GitOperation).filter(GitOperation.repository_id == repo_id)
            .order_by(GitOperation.id.desc()).first())


def _out(db: Session, repo: GitRepository, owner: User, current_user: User) -> dict:
    git_repos.settle_stale(db, repo)
    return git_repos.serialize(repo, owner, _last_operation(db, repo.id),
                               hide_secrets=demo_mode.is_demo_account(current_user))


def _check_auth(auth_type: str, url: str, token_present: bool) -> None:
    if auth_type == "ssh" and url and not git_repos.url_is_ssh(url):
        raise ValueError("A deploy key needs an SSH URL, such as git@github.com:owner/repo.git")
    if auth_type == "https" and url and not url.startswith("https://"):
        raise ValueError("An HTTPS token needs an https:// URL")
    if auth_type == "https" and not token_present:
        raise ValueError("Enter the HTTPS token")


def _start(db: Session, repo: GitRepository, action: str, actor: User, **params) -> GitOperation:
    try:
        return git_repos.start(db, repo, action, actor, **params)
    except ValueError as exc:
        raise _fail(exc) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/info")
def git_info(current_user: User = Depends(get_current_user)):
    return {"enabled": git_repos.enabled()}


@guarded.get("/overview")
def overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = scope_owner(db.query(GitRepository), GitRepository.owner_id, db, current_user)
    repos = query.order_by(GitRepository.name.asc(), GitRepository.id.asc()).all()
    owners = {u.id: u for u in db.query(User).filter(User.id.in_({r.owner_id for r in repos})).all()} if repos else {}
    return {
        "repos": [_out(db, repo, owners[repo.owner_id], current_user) for repo in repos if repo.owner_id in owners],
        "home": str(git_repos.home_for(current_user)),
        "presets": [{"key": key, "command": command} for key, command in git_repos.PRESETS.items()],
        "php_versions": list(git_repos.PHP_VERSIONS),
        "default_php_version": settings.default_php_version,
        "max_repos": git_repos.MAX_REPOS_PER_OWNER,
    }


@guarded.post("/repos")
def create_repo(payload: RepoCreate, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    owner = current_user
    if payload.owner_id and payload.owner_id != current_user.id:
        owner = db.get(User, payload.owner_id)
        if not owner or not can_access_owner(db, current_user, owner.id):
            raise HTTPException(status_code=404, detail="User not found")
    try:
        if db.query(GitRepository).filter(GitRepository.owner_id == owner.id).count() >= git_repos.MAX_REPOS_PER_OWNER:
            raise ValueError(f"An account can have at most {git_repos.MAX_REPOS_PER_OWNER} repositories")
        name = git_repos.validate_name(payload.name)
        path = git_repos.resolve_path(owner, payload.path)
        git_repos.check_overlap(db, path)
        branch = git_repos.validate_branch(payload.branch)
        url = git_repos.validate_url(payload.remote_url, required=payload.mode == "clone")
        token = payload.https_token.strip()
        _check_auth(payload.auth_type, url, bool(token))
        https_username = payload.https_username.strip()
        if https_username and not git_repos.HTTPS_USER_RE.fullmatch(https_username):
            raise ValueError("Invalid HTTPS username")
        repo = GitRepository(
            owner_id=owner.id, name=name, path=path, remote_url=url, branch=branch,
            auth_type=payload.auth_type,
            https_username=https_username if payload.auth_type == "https" else "",
            https_token=encrypt(token) if payload.auth_type == "https" else "",
            deploy_commands=json.dumps(git_repos.validate_presets(payload.deploy_commands)),
            php_version=git_repos.validate_php(payload.php_version),
            webhook_token=git_repos.new_webhook_token(),
        )
        if payload.auth_type == "ssh":
            git_repos.set_ssh_key(repo, owner)
    except ValueError as exc:
        raise _fail(exc) from exc
    db.add(repo)
    db.commit()
    db.refresh(repo)
    log_action(db, current_user.id, "create_git_repository", f"{owner.username}:{repo.name}", repo.path, request=request)
    # A clone with a deploy key waits for the key to be added on the remote.
    if not (payload.mode == "clone" and payload.auth_type == "ssh"):
        _start(db, repo, payload.mode, current_user)
    return _out(db, repo, owner, current_user)


@guarded.get("/repos/{repo_id}")
def repo_detail(repo_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    repo, owner = _repo_for(db, repo_id, current_user)
    status, status_error = None, ""
    if repo.ready and not git_repos.is_busy(repo.id):
        try:
            status = git_repos.status(repo, owner)
        except RuntimeError as exc:
            status_error = str(exc)
    operations = (db.query(GitOperation).filter(GitOperation.repository_id == repo.id)
                  .order_by(GitOperation.id.desc()).limit(20).all())
    return {
        "repo": _out(db, repo, owner, current_user),
        "status": status,
        "status_error": status_error,
        "operations": [git_repos.serialize_operation(op) for op in operations],
    }


@guarded.get("/repos/{repo_id}/operations/{operation_id}")
def operation_detail(repo_id: int, operation_id: int, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    repo, _ = _repo_for(db, repo_id, current_user)
    operation = db.get(GitOperation, operation_id)
    if not operation or operation.repository_id != repo.id:
        raise HTTPException(status_code=404, detail="Operation not found")
    return git_repos.serialize_operation(operation, with_log=True)


@guarded.patch("/repos/{repo_id}")
def update_repo(repo_id: int, payload: RepoUpdate, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    repo, owner = _repo_for(db, repo_id, current_user)
    if git_repos.is_busy(repo.id):
        raise HTTPException(status_code=409, detail="Wait for the running operation to finish")
    try:
        if payload.name is not None:
            repo.name = git_repos.validate_name(payload.name)
        url = repo.remote_url
        if payload.remote_url is not None:
            url = git_repos.validate_url(payload.remote_url, required=False)
        auth_type = payload.auth_type or repo.auth_type
        token = (payload.https_token or "").strip()
        _check_auth(auth_type, url, bool(token) or (auth_type == repo.auth_type == "https" and bool(repo.https_token)))
        if payload.https_username is not None:
            username = payload.https_username.strip()
            if username and not git_repos.HTTPS_USER_RE.fullmatch(username):
                raise ValueError("Invalid HTTPS username")
            repo.https_username = username
        if auth_type != "https":
            repo.https_token, repo.https_username = "", ""
        elif token:
            repo.https_token = encrypt(token)
        if auth_type == "ssh" and not repo.ssh_private_key:
            git_repos.set_ssh_key(repo, owner)
        if auth_type != "ssh":
            repo.ssh_private_key, repo.ssh_public_key = "", ""
        repo.auth_type = auth_type
        if payload.deploy_commands is not None:
            repo.deploy_commands = json.dumps(git_repos.validate_presets(payload.deploy_commands))
        if payload.php_version is not None:
            repo.php_version = git_repos.validate_php(payload.php_version)
        if payload.webhook_enabled is not None:
            repo.webhook_enabled = payload.webhook_enabled
            if not repo.webhook_token:
                repo.webhook_token = git_repos.new_webhook_token()
        if url != repo.remote_url:
            git_repos.set_remote(repo, owner, url)
            repo.remote_url = url
    except ValueError as exc:
        db.rollback()
        raise _fail(exc) from exc
    except RuntimeError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    log_action(db, current_user.id, "update_git_repository", f"{owner.username}:{repo.name}", repo.path, request=request)
    return _out(db, repo, owner, current_user)


@guarded.post("/repos/{repo_id}/start")
def start_repo(repo_id: int, payload: StartIn, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """Clone (after the deploy key was added), or try a failed clone/init again."""
    repo, owner = _repo_for(db, repo_id, current_user)
    if repo.ready:
        raise HTTPException(status_code=400, detail="The repository is already set up")
    if payload.mode == "clone" and not repo.remote_url:
        raise HTTPException(status_code=400, detail="Set the repository URL first")
    operation = _start(db, repo, payload.mode, current_user)
    log_action(db, current_user.id, f"git_{payload.mode}", f"{owner.username}:{repo.name}", repo.path, request=request)
    return git_repos.serialize_operation(operation)


@guarded.post("/repos/{repo_id}/deploy")
def deploy_repo(repo_id: int, payload: DeployIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    repo, owner = _repo_for(db, repo_id, current_user)
    if not repo.remote_url:
        raise HTTPException(status_code=400, detail="This repository has no remote to pull from")
    operation = _start(db, repo, "deploy", current_user, discard_local=payload.discard_local,
                       run_commands=payload.run_commands)
    log_action(db, current_user.id, "git_deploy", f"{owner.username}:{repo.name}",
               "discard local changes" if payload.discard_local else "", request=request)
    return git_repos.serialize_operation(operation)


@guarded.post("/repos/{repo_id}/push")
def push_repo(repo_id: int, payload: PushIn, request: Request, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    repo, owner = _repo_for(db, repo_id, current_user)
    message = payload.message.strip()
    if not message or "\n" in message or "\r" in message:
        raise HTTPException(status_code=400, detail="The commit message must be one line")
    if not repo.remote_url:
        raise HTTPException(status_code=400, detail="This repository has no remote to push to")
    operation = _start(db, repo, "push", current_user, message=message)
    log_action(db, current_user.id, "git_push", f"{owner.username}:{repo.name}", message[:200], request=request)
    return git_repos.serialize_operation(operation)


@guarded.post("/repos/{repo_id}/checkout")
def checkout_repo(repo_id: int, payload: CheckoutIn, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    repo, owner = _repo_for(db, repo_id, current_user)
    try:
        branch = git_repos.validate_branch(payload.branch)
    except ValueError as exc:
        raise _fail(exc) from exc
    operation = _start(db, repo, "checkout", current_user, branch=branch)
    log_action(db, current_user.id, "git_checkout", f"{owner.username}:{repo.name}", branch, request=request)
    return git_repos.serialize_operation(operation)


@guarded.post("/repos/{repo_id}/ssh-key")
def regenerate_ssh_key(repo_id: int, request: Request, db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    repo, owner = _repo_for(db, repo_id, current_user)
    if repo.auth_type != "ssh":
        raise HTTPException(status_code=400, detail="This repository does not use a deploy key")
    git_repos.set_ssh_key(repo, owner)
    db.commit()
    log_action(db, current_user.id, "git_ssh_key", f"{owner.username}:{repo.name}", "", request=request)
    return _out(db, repo, owner, current_user)


@guarded.post("/repos/{repo_id}/webhook-token")
def rotate_webhook_token(repo_id: int, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    repo, owner = _repo_for(db, repo_id, current_user)
    repo.webhook_token = git_repos.new_webhook_token()
    db.commit()
    log_action(db, current_user.id, "git_webhook_token", f"{owner.username}:{repo.name}", "", request=request)
    return _out(db, repo, owner, current_user)


@guarded.delete("/repos/{repo_id}")
def delete_repo(repo_id: int, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    repo, owner = _repo_for(db, repo_id, current_user)
    if git_repos.is_busy(repo.id):
        raise HTTPException(status_code=409, detail="Wait for the running operation to finish")
    label, path = f"{owner.username}:{repo.name}", repo.path
    git_repos.delete(db, repo)
    db.commit()
    log_action(db, current_user.id, "delete_git_repository", label, path, request=request)
    return {"ok": True}


# --- Webhook: no session, the token in the URL is the credential ------------------------

@guarded.post("/hook/{repo_id}/{token}")
async def webhook(repo_id: int, token: str, request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    if len(body) > WEBHOOK_BODY_LIMIT:
        raise HTTPException(status_code=413, detail="Payload too large")
    repo = git_repos.webhook_repository(db, repo_id, token)
    if not repo:
        raise HTTPException(status_code=404, detail="Not found")
    if not git_repos.webhook_signature_ok(repo, body, request.headers):
        raise HTTPException(status_code=403, detail="Signature does not match")
    if request.headers.get("x-github-event") == "ping":
        return {"ok": True, "message": "pong"}
    payload: object = {}
    try:
        if body.startswith(b"payload="):
            payload = json.loads(parse_qs(body.decode("utf-8")).get("payload", ["{}"])[0])
        elif body:
            payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        payload = {}
    branch = git_repos.webhook_branch(payload)
    if branch is not None and branch != repo.branch:
        return {"ok": True, "deployed": False, "message": f"Push to {branch}; this repository deploys {repo.branch}"}
    if not repo.remote_url:
        return {"ok": True, "deployed": False, "message": "No remote to pull from"}
    try:
        operation = git_repos.start(db, repo, "deploy", None, trigger="webhook", discard_local=False, run_commands=True)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "deployed": True, "operation_id": operation.id}


router.include_router(guarded)
