"""Git repositories in an account's home (operator, 2026-10-09: "Repo tự do
trong home của user", deploy key and HTTPS token, Deploy button and webhook,
pull or push and preset commands).

What must hold: git only ever runs as the account's own Linux user; a path
stays inside that account's home and out of hidden folders; a remote is
https or ssh and nothing else; credentials never reach an argument list;
only preset commands run; an account sees its own repositories only; the
webhook needs its token and deploys only the branch it tracks.
"""
import hashlib
import hmac
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import GitOperation, GitRepository, User
from app.services import git_repos

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


class InlineExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("root_admin", "admin"), ("alice", "end_user"), ("bob", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    calls = []

    def fake_privileged(command, helper_args=None, check=True, input=None, sensitive=False, fallback=None):
        calls.append({"command": command, "args": helper_args, "input": input, "sensitive": sensitive})
        out = 'OPANEL_GIT_RESULT {"ok": true, "before": "a1", "after": "b2"}\n'
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(git_repos.shell, "privileged", fake_privileged)
    monkeypatch.setattr(git_repos.settings, "command_dry_run", False)
    monkeypatch.setattr(git_repos, "_executor", InlineExecutor())
    monkeypatch.setattr(git_repos, "_session_factory", Session)
    monkeypatch.setattr(git_repos, "generate_ssh_key", lambda comment: ("PRIVATE-KEY", f"ssh-ed25519 AAAA {comment}"))

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    state = {"user": people["alice"]}
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)

    def as_user(name):
        state["user"] = db.get(User, people[name].id)
        return client

    try:
        yield SimpleNamespace(db=db, client=client, people=people, calls=calls, as_user=as_user)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def _create(env, user="alice", **body):
    payload = {"name": "shop", "path": "apps/shop", "mode": "clone",
               "remote_url": "https://github.com/acme/shop.git", "branch": "main"}
    payload.update(body)
    res = env.as_user(user).post("/api/git/repos", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


# --- Validation ------------------------------------------------------------------------

@pytest.mark.parametrize("relative", ["", "/", ".ssh", "apps/.git", "a/../b", "-x", "a/" * 9 + "b", "a b"])
def test_a_path_stays_in_the_home_and_out_of_hidden_folders(relative):
    owner = User(username="alice")
    with pytest.raises(ValueError):
        git_repos.resolve_path(owner, relative)


def test_a_path_is_resolved_inside_the_accounts_home():
    owner = User(username="alice")
    assert git_repos.resolve_path(owner, "example.com/public_html/") == "/home/alice/example.com/public_html"


@pytest.mark.parametrize("url", ["https://github.com/acme/shop.git", "git@github.com:acme/shop.git",
                                 "ssh://git@gitlab.example.com:2222/acme/shop.git"])
def test_https_and_ssh_remotes_are_accepted(url):
    assert git_repos.validate_url(url) == url


@pytest.mark.parametrize("url", ["file:///etc", "/srv/repo.git", "-uploadpack=touch", "ext::sh -c id",
                                 "http://github.com/acme/shop.git", "https://user:tok@github.com/acme/shop.git",
                                 "git@github.com:-acme/shop.git", "https://github.com/acme/shop.git; id"])
def test_other_remotes_are_refused(url):
    with pytest.raises(ValueError):
        git_repos.validate_url(url)


@pytest.mark.parametrize("branch", ["main", "release/1.2", "feature_x"])
def test_branches(branch):
    assert git_repos.validate_branch(branch) == branch


@pytest.mark.parametrize("branch", ["-x", "a..b", "a//b", "a/", "x.lock", "a/.b", "a@{1}", "a b"])
def test_bad_branches(branch):
    with pytest.raises(ValueError):
        git_repos.validate_branch(branch)


def test_only_preset_commands_run():
    assert git_repos.validate_presets(["npm-ci", "npm-build", "npm-ci"]) == ["npm-ci", "npm-build"]
    with pytest.raises(ValueError):
        git_repos.validate_presets(["rm -rf /"])
    body = HELPER.split("\ngit_repo() {", 1)[1]
    helper_presets = body.split('[[ "$1" =~ ^(', 1)[1].split(")$ ]]", 1)[0].split("|")
    assert helper_presets == list(git_repos.PRESETS), "the helper and the panel offer the same commands"


# --- The helper ------------------------------------------------------------------------

def test_git_runs_as_the_accounts_user_never_as_root():
    body = HELPER.split("\ngit_repo() {", 1)[1].split("\nPY\n}", 1)[0]
    assert 'runuser -u "$user" -- env -i' in body
    assert "GIT_ALLOW_PROTOCOL=https:ssh" in body and "GIT_CONFIG_NOSYSTEM=1" in body
    assert "GIT_TERMINAL_PROMPT=0" in body
    # Nothing but the runuser'd python calls git: no shell line runs it.
    shell_part = body.split("<<'PY'", 1)[0]
    commands = [line.strip() for line in shell_part.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert not [line for line in commands if re.match(r"(exec\s+)?git\s", line)]


def test_credentials_arrive_on_stdin_and_never_in_arguments():
    body = HELPER.split("\ngit_repo() {", 1)[1].split("\nPY\n}", 1)[0]
    assert 'IFS= read -r auth_kind' in body and 'cat >"$run_dir/key"' in body
    assert "GIT_CONFIG_KEY_0=credential.helper" in body, "the token goes through a credential helper"
    assert "askpass" not in body.lower().replace("askpass_env", ""), "nothing to execute from /run (noexec)"
    assert "trap \"rm -rf -- '$run_dir'\" EXIT" in body, "expanded now: run_dir is local"
    repo = GitRepository(auth_type="https", https_username="bot", https_token=git_repos.encrypt("tok"))
    assert git_repos.credentials(repo) == "https\nbot\ntok"
    assert git_repos.credentials(GitRepository(auth_type="none")) == "none\n"


def test_the_helper_checks_paths_urls_and_branches():
    path_check = HELPER.split("\nrequire_git_path() {", 1)[1].split("\n}\n", 1)[0]
    assert 'require_safe_path "$HOME_ROOT/$user" "$path") || exit 1' in path_check
    assert "^[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$" in path_check
    prepare = HELPER.split("\ngit_prepare_path() {", 1)[1].split("\n}\n", 1)[0]
    assert 'if [[ -L "$first" ]]' in prepare and 'runuser -u "$user" -- mkdir -p -- "$repo"' in prepare
    assert "  git-repo)\n    git_repo \"$@\"" in HELPER


# --- The API ---------------------------------------------------------------------------

def test_a_clone_runs_as_the_owner_with_its_credentials(env):
    repo = _create(env, auth_type="https", https_username="bot", https_token="secret-token")
    clone = env.calls[-1]
    assert clone["command"] == "git-repo"
    assert clone["args"] == ["clone", "alice", "/home/alice/apps/shop", "https://github.com/acme/shop.git", "main"]
    assert clone["input"] == "https\nbot\nsecret-token" and clone["sensitive"]
    assert "secret-token" not in json.dumps(repo), "the token never comes back"
    row = env.db.get(GitRepository, repo["id"])
    env.db.refresh(row)
    assert row.ready and row.https_token != "secret-token"


def test_a_deploy_key_clone_waits_for_the_key(env):
    repo = _create(env, auth_type="ssh", remote_url="git@github.com:acme/shop.git")
    assert repo["ssh_public_key"].startswith("ssh-ed25519 ") and not repo["ready"]
    assert env.calls == [], "nothing is cloned before the key is on the remote"
    res = env.as_user("alice").post(f"/api/git/repos/{repo['id']}/start", json={"mode": "clone"})
    assert res.status_code == 200, res.text
    assert env.calls[-1]["input"] == "ssh\nPRIVATE-KEY"


def test_a_deploy_key_needs_an_ssh_url(env):
    res = env.as_user("alice").post("/api/git/repos", json={
        "name": "x", "path": "x", "remote_url": "https://github.com/acme/x.git", "auth_type": "ssh"})
    assert res.status_code == 400 and "SSH URL" in res.json()["detail"]


def test_deploy_pulls_then_runs_the_presets_in_order(env):
    repo = _create(env, deploy_commands=["composer-install", "artisan-migrate"], php_version="8.3")
    env.calls.clear()
    res = env.as_user("alice").post(f"/api/git/repos/{repo['id']}/deploy", json={"discard_local": True})
    assert res.status_code == 200, res.text
    assert [c["args"][0] for c in env.calls] == ["pull", "run", "run"]
    assert env.calls[0]["args"][3:] == ["main", "reset"]
    assert env.calls[1]["args"][3:] == ["composer-install", "8.3"]
    assert env.calls[2]["args"][3:] == ["artisan-migrate", "8.3"]
    operation = env.db.query(GitOperation).order_by(GitOperation.id.desc()).first()
    assert operation.status == "done" and operation.commit_after == "b2"


def test_push_commits_as_the_person_who_pushed(env):
    repo = _create(env)
    env.calls.clear()
    res = env.as_user("alice").post(f"/api/git/repos/{repo['id']}/push", json={"message": "fix header"})
    assert res.status_code == 200, res.text
    assert env.calls[0]["args"][3:] == ["main", "alice", "alice@example.test", "fix header"]
    bad = env.as_user("alice").post(f"/api/git/repos/{repo['id']}/push", json={"message": "a\nb"})
    assert bad.status_code == 400


def test_an_account_sees_its_own_repositories_only(env):
    repo = _create(env)
    assert env.as_user("bob").get(f"/api/git/repos/{repo['id']}").status_code == 404
    assert env.as_user("bob").post(f"/api/git/repos/{repo['id']}/deploy", json={}).status_code == 404
    assert env.as_user("bob").get("/api/git").json()["repos"] == []
    assert [r["id"] for r in env.as_user("root_admin").get("/api/git").json()["repos"]] == [repo["id"]]


def test_one_repository_per_folder_and_none_inside_another(env):
    _create(env)
    res = env.as_user("alice").post("/api/git/repos", json={
        "name": "inner", "path": "apps/shop/vendor", "remote_url": "https://github.com/acme/v.git"})
    assert res.status_code == 400


def test_the_webhook_needs_its_token_and_deploys_the_tracked_branch(env):
    repo = _create(env)
    env.as_user("alice").patch(f"/api/git/repos/{repo['id']}", json={"webhook_enabled": True})
    detail = env.as_user("alice").get(f"/api/git/repos/{repo['id']}").json()["repo"]
    path = detail["webhook_path"]
    assert path.startswith(f"/api/git/hook/{repo['id']}/")
    env.calls.clear()
    assert env.client.post(f"/api/git/hook/{repo['id']}/wrong", json={}).status_code == 404
    other = env.client.post(path, json={"ref": "refs/heads/dev"})
    assert other.status_code == 200 and other.json()["deployed"] is False and env.calls == []
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    token = path.rsplit("/", 1)[1]
    bad_sig = env.client.post(path, content=body, headers={"x-hub-signature-256": "sha256=00"})
    assert bad_sig.status_code == 403
    good_sig = "sha256=" + hmac.new(token.encode(), body, hashlib.sha256).hexdigest()
    res = env.client.post(path, content=body, headers={"x-hub-signature-256": good_sig, "content-type": "application/json"})
    assert res.status_code == 200 and res.json()["deployed"] is True
    assert env.calls[0]["args"][0] == "pull"
    operation = env.db.query(GitOperation).order_by(GitOperation.id.desc()).first()
    assert operation.trigger == "webhook"


def test_a_disabled_webhook_is_not_found(env):
    repo = _create(env)
    row = env.db.get(GitRepository, repo["id"])
    assert env.client.post(f"/api/git/hook/{repo['id']}/{row.webhook_token}", json={}).status_code == 404


def test_deleting_keeps_the_files(env):
    repo = _create(env)
    env.calls.clear()
    res = env.as_user("alice").delete(f"/api/git/repos/{repo['id']}")
    assert res.status_code == 200
    assert env.calls == [], "the panel forgets the repository; nothing on disk is touched"
    assert env.db.query(GitRepository).count() == 0 and env.db.query(GitOperation).count() == 0


def test_webhook_branch_formats():
    assert git_repos.webhook_branch({"ref": "refs/heads/main"}) == "main"
    assert git_repos.webhook_branch({"push": {"changes": [{"new": {"type": "branch", "name": "prod"}}]}}) == "prod"
    assert git_repos.webhook_branch({"ref": "refs/tags/v1"}) is None
    assert git_repos.webhook_branch("x") is None


def test_an_operation_cut_off_by_a_restart_is_not_shown_running(env):
    repo = _create(env)
    env.db.add(GitOperation(repository_id=repo["id"], action="deploy", status="running"))
    env.db.commit()
    detail = env.as_user("alice").get(f"/api/git/repos/{repo['id']}").json()
    assert detail["operations"][0]["status"] == "failed"
    assert not detail["repo"]["busy"]
