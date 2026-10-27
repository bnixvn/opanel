"""Redis per account (operator, 2026-10-27, as DirectAdmin has it).

Each hosting account may turn on its own Redis: an instance of
opanel-redis@.service as the account's Linux user, on a unix socket in its
home and no TCP port. The administrator gives a package (or an account
without one) its memory, 0 = not allowed. These pin the helper's validation
and what it writes, that the server's own Redis is never touched, the limit a
package or an account gives, the API's refusals, and that an instance follows
its account when the package, the limit or the suspension changes.
"""
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import HostingPlan, User
from app.services import addons, demo_mode, redis_user, resource_limits, site_users

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
PASSWORD = "PasswordLongEnough1"
ACCOUNT = {"email": "x@example.com", "password": PASSWORD}
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _function(name: str) -> str:
    start = HELPER.index(f"\n{name}() {{") + 1
    return HELPER[start: HELPER.index("\n}\n", start)] + "\n}\n"


def _code(text: str) -> str:
    """*text* without its comment lines."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _block() -> str:
    start = HELPER.index("# --- Redis per account")
    return HELPER[start: HELPER.index("\nredis_user_flush() {", start)] + _function("redis_user_flush")


def _case(name: str) -> str:
    start = HELPER.index(f"\n  {name})\n")
    return HELPER[start: HELPER.index("\n    ;;\n", start)]


# --- the helper ---------------------------------------------------------------------------

def test_the_helper_knows_the_addon_and_dispatches_every_verb():
    assert re.search(r'ADDON_IDS=\([^)]*"redis"', HELPER)
    for verb, handler in (("addon-status", "addon_redis_status"), ("addon-install", "addon_redis_install"),
                          ("addon-uninstall", "addon_redis_uninstall"), ("addon-enable", "addon_redis_enable"),
                          ("addon-disable", "addon_redis_disable")):
        assert f"redis) {handler} ;;" in _case(verb), verb
    assert 'deny "usage: redis-user-apply <user> <maxmemory_mb> [slice]"' in _case("redis-user-apply")
    for verb in ("redis-user-remove", "redis-user-status", "redis-user-flush"):
        assert '[[ $# -eq 1 ]] || deny' in _case(verb), verb


def test_an_account_is_checked_before_anything_is_written():
    body = _function("redis_user_apply")
    first_write = body.index("install -d")
    for check in ("require_redis_installed", 'require_terminal_user "$user"', 'require_redis_mb "$mb_arg"',
                  "REDIS_SLICE_RE", "REDIS_STOPPED_MARKER"):
        assert body.index(check) < first_write, check
    for verb in ("redis_user_status", "redis_user_flush"):
        assert 'require_terminal_user "$user"' in _function(verb)
    assert 'require_linux_user "$user"' in _function("redis_user_remove")


def test_the_socket_folder_and_the_socket_are_the_accounts_alone():
    """0700 and a 0700 socket: the sites' PHP runs as the account itself, so
    no group needs in. Root makes the folder - the home is root's, so the
    account could not, nor swap it for a link (operator review, 2026-10-27)."""
    body = _code(_function("redis_user_apply"))
    assert 'if [[ -L "$dir" ]] || { [[ -e "$dir" ]] && [[ ! -d "$dir" ]]; }; then' in body
    assert body.index('deny "$dir is not a directory"') < body.index('install -d -o "$user" -g "$group" -m 0700 "$dir"')
    assert "0750" not in body
    assert "unixsocketperm 700" in _function("redis_user_config")


@needs_bash
def test_a_link_in_place_of_the_folder_is_refused(tmp_path):
    (tmp_path / "home" / "alice").mkdir(parents=True)
    (tmp_path / "home" / "alice" / ".redis").write_text("not a folder", encoding="utf-8")
    result, log = _run_helper(tmp_path, "redis_user_apply alice 64")
    assert result.returncode != 0 and "is not a directory" in result.stderr
    assert "enable" not in log


def test_the_unit_runs_as_the_account_with_its_home_read_only_but_the_socket_dir():
    unit = _function("redis_unit_text")
    for line in ("Type=notify", "User=%i", "Group=%i", "ExecStart=/usr/bin/redis-server /etc/opanel-redis/%i.conf",
                 "Restart=on-failure", "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=strict",
                 "ProtectHome=read-only", "ReadWritePaths=/home/%i/.redis", "LimitNOFILE=1024",
                 "WantedBy=multi-user.target"):
        assert f"\n{line}\n" in unit, line


def test_the_servers_own_redis_is_never_touched():
    code = _code(_block())
    assert "redis-server.service" not in code
    assert not re.search(r"systemctl[^\n]*\bredis-server\b", code)
    assert "purge" not in code and "apt-get remove" not in code and "autoremove" not in code
    # apt only when the binary is not there at all.
    install = _function("addon_redis_install")
    assert install.index('if [[ ! -x "$REDIS_SERVER_BIN" ]]') < install.index("install -y redis-server")
    # Every unit it starts, stops or removes is one of its own instances.
    for match in re.finditer(r"systemctl (?:enable|disable|restart|stop|start)[^\n]*", code):
        assert "opanel-redis@" in match.group(0), match.group(0)


def test_every_update_refreshes_the_template_unit():
    hygiene = HELPER[HELPER.index("  log-hygiene)"):]
    assert "( redis_refresh )" in hygiene[: hygiene.index(";;")]
    refresh = _function("redis_refresh")
    assert "redis_installed || return 0" in refresh
    assert refresh.rstrip().endswith("return 0\n}")


def test_the_status_line_reports_what_the_panel_reads():
    body = _function("addon_redis_status")
    assert 'echo "installed=${installed} running=${running} enabled=${enabled} version=${version} active=${active}"' in body
    assert "opanel-redis@*.service" in body


def test_stop_keeps_the_configurations_and_uninstall_removes_everything_of_its_own():
    stop = _function("addon_redis_disable")
    assert 'systemctl disable --now "opanel-redis@${user}.service"' in stop
    assert "rm " not in _code(stop)
    start = _function("addon_redis_enable")
    assert 'systemctl enable --now "opanel-redis@${user}.service"' in start
    gone = _function("addon_redis_uninstall")
    for part in ('rm -rf "$REDIS_CONF_DIR"', 'rm -f "$REDIS_UNIT"', 'rm -rf -- "$HOME_ROOT/$user/.redis"',
                 'systemctl disable --now "opanel-redis@${user}.service"'):
        assert part in gone, part


def _run_helper(tmp_path, *lines, active=False):
    """The helper's Redis functions in bash, with systemctl, install's owner
    and mode flags (Windows) and the account check stubbed and every path in
    tmp_path."""
    base = tmp_path.as_posix()
    (tmp_path / "home" / "alice").mkdir(parents=True, exist_ok=True)
    binary = tmp_path / "redis-server"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    (tmp_path / "systemd").mkdir(exist_ok=True)
    (tmp_path / "systemd" / "opanel-redis@.service").write_text("unit\n", encoding="utf-8")
    if active:
        (tmp_path / "active").write_text("", encoding="utf-8")
    functions = "".join(_function(name) for name in (
        "redis_installed", "require_redis_installed", "require_redis_mb", "redis_user_config",
        "redis_user_dropin", "redis_install_if_changed", "redis_user_apply", "redis_user_remove"))
    script = "\n".join([
        "set -euo pipefail",
        f'HOME_ROOT="{base}/home"',
        f'REDIS_SERVER_BIN="{base}/redis-server"',
        f'REDIS_UNIT_DIR="{base}/systemd"',
        'REDIS_UNIT="$REDIS_UNIT_DIR/opanel-redis@.service"',
        f'REDIS_CONF_DIR="{base}/etc-opanel-redis"',
        'REDIS_STOPPED_MARKER="$REDIS_CONF_DIR/stopped"',
        "REDIS_MAX_MB=4096",
        "REDIS_SLICE_RE='^hosting(-r[0-9]{1,9})?-a[0-9]{1,9}\\.slice$'",
        f'LOG="{base}/log"',
        'deny() { echo "opanel-helper: $*" >&2; exit 1; }',
        'require_terminal_user() { if [[ "$1" != "alice" ]]; then deny "not a hosting account user: $1"; fi; }',
        'require_linux_user() { if [[ ! "$1" =~ ^[a-z_][a-z0-9_-]{2,31}$ ]]; then deny "invalid panel Linux user: $1"; fi; }',
        'id() { if [[ "$1" == "-gn" ]]; then echo "$2"; else command id "$@"; fi; }',
        f'systemctl() {{ echo "systemctl $*" >>"$LOG"; if [[ "$1" == "is-active" ]]; then [[ -f "{base}/active" ]]; return; fi; return 0; }}',
        'install() { local args=() skip=0 a; for a in "$@"; do if (( skip )); then skip=0; continue; fi; '
        'case "$a" in -o|-g|-m) skip=1 ;; *) args+=("$a") ;; esac; done; command install "${args[@]}"; }',
        functions,
        *lines,
    ])
    (tmp_path / "log").write_text("", encoding="utf-8")
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return result, (tmp_path / "log").read_text(encoding="utf-8")




@needs_bash
def test_apply_writes_the_cache_only_config_and_starts_the_instance(tmp_path):
    result, log = _run_helper(tmp_path, "redis_user_apply alice 64 hosting-r3-a7.slice")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "applied"
    base = tmp_path.as_posix()
    conf = (tmp_path / "etc-opanel-redis" / "alice.conf").read_text(encoding="utf-8")
    settings = [line for line in conf.splitlines() if line and not line.startswith("#")]
    assert settings == [
        "port 0",
        f"unixsocket {base}/home/alice/.redis/redis.sock",
        "unixsocketperm 700",
        f"dir {base}/home/alice/.redis",
        "maxmemory 64mb",
        "maxmemory-policy allkeys-lru",
        'save ""',
        "appendonly no",
        "supervised systemd",
        "daemonize no",
        'logfile ""',
        "databases 16",
        "protected-mode yes",
    ]
    dropin = (tmp_path / "systemd" / "opanel-redis@alice.service.d" / "memory.conf").read_text(encoding="utf-8")
    # A little above maxmemory: 64 * 1.25 + 16.
    assert "\nMemoryMax=96M\n" in dropin and "\nSlice=hosting-r3-a7.slice\n" in dropin
    assert (tmp_path / "home" / "alice" / ".redis").is_dir()
    assert "systemctl daemon-reload" in log
    assert "systemctl enable --now opanel-redis@alice.service" in log
    assert "restart" not in log


@needs_bash
def test_apply_restarts_only_when_something_changed(tmp_path):
    result, _log = _run_helper(tmp_path, "redis_user_apply alice 64")
    assert result.stdout.strip() == "applied", result.stderr
    result, log = _run_helper(tmp_path, "redis_user_apply alice 64", active=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "unchanged"
    assert "restart" not in log, "an unchanged apply must not empty the cache"
    assert "systemctl enable --now opanel-redis@alice.service" in log

    result, log = _run_helper(tmp_path, "redis_user_apply alice 128", active=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "applied"
    assert "systemctl restart opanel-redis@alice.service" in log
    assert "maxmemory 128mb" in (tmp_path / "etc-opanel-redis" / "alice.conf").read_text(encoding="utf-8")
    assert "MemoryMax=176M" in (tmp_path / "systemd" / "opanel-redis@alice.service.d" / "memory.conf").read_text(encoding="utf-8")


@needs_bash
@pytest.mark.parametrize("args", [
    "alice 0", "alice 4097", "alice abc", "alice -5", "alice 64mb", "'alice' '64;id'",
    "alice 64 system.slice", "alice 64 ../hosting-a1.slice", "alice 64 'hosting-a1.slice;id'",
    "bob 64", "root 64",
])
def test_apply_refuses_bad_input_and_writes_nothing(tmp_path, args):
    result, log = _run_helper(tmp_path, f"redis_user_apply {args}")
    assert result.returncode != 0
    assert "opanel-helper:" in result.stderr
    assert not (tmp_path / "etc-opanel-redis").exists()
    assert "enable" not in log


@needs_bash
def test_apply_refuses_while_the_addon_is_stopped(tmp_path):
    (tmp_path / "etc-opanel-redis").mkdir()
    (tmp_path / "etc-opanel-redis" / "stopped").write_text("", encoding="utf-8")
    result, log = _run_helper(tmp_path, "redis_user_apply alice 64")
    assert result.returncode != 0 and "stopped" in result.stderr
    assert "enable" not in log


@needs_bash
def test_remove_takes_the_instance_its_config_and_its_socket_dir(tmp_path):
    result, log = _run_helper(tmp_path, "redis_user_apply alice 64", "redis_user_remove alice")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["applied", "removed"]
    assert "systemctl disable --now opanel-redis@alice.service" in log
    assert not (tmp_path / "etc-opanel-redis" / "alice.conf").exists()
    assert not (tmp_path / "systemd" / "opanel-redis@alice.service.d").exists()
    assert not (tmp_path / "home" / "alice" / ".redis").exists()
    result, _log = _run_helper(tmp_path, "redis_user_remove '../alice'")
    assert result.returncode != 0


# --- the registry ----------------------------------------------------------------------------

def test_the_addon_is_registered_off_by_default_and_run_by_the_helper(monkeypatch, tmp_path):
    entry = addons.ADDONS["redis"]
    assert entry["name"] == "Redis per account" and entry["category"] == "hosting"
    assert entry.get("kind") is None, "managed by the helper, like limits"
    assert "redis" in addons.helper_addon_ids()
    notes = " ".join(entry["notes"])
    for part in ("/home/<user>/.redis/redis.sock", "nothing is saved to disk", "0 MB", "127.0.0.1:6379"):
        assert part in notes, part

    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons.json")
    monkeypatch.setattr(addons.shell, "privileged", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="installed=0 running=0 enabled=0 version= active=0", stderr=""))
    assert addons.status("redis")["installed"] is False
    monkeypatch.setattr(redis_user, "UNIT_FILE", tmp_path / "opanel-redis@.service")
    assert redis_user.installed() is False


def test_the_page_and_its_fields_appear_only_with_the_addon():
    features = (ROOT / "frontend" / "src" / "lib" / "features.jsx").read_text(encoding="utf-8")
    assert "redis: { label: 'Redis', icon: 'cache', page: 'redis', addon: 'redis'" in features
    app_jsx = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "const redisOn = !!redisInfo?.installed;" in app_jsx
    assert app_jsx.count('{redisOn && ') >= 2, "the package and the account forms"


# --- the panel ----------------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(User(username="root_admin", email="a@example.com", role="admin", is_active=True,
                hashed_password=hash_password(PASSWORD)))
    db.add(User(username="direct", email="d@example.com", role="end_user", is_active=True,
                hashed_password=hash_password(PASSWORD)))
    db.commit()

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    calls = []

    def privileged(command, helper_args=None, check=True, input=None, sensitive=False, fallback=None):
        calls.append([command, *(helper_args or [])])
        if command == "redis-user-status":
            return SimpleNamespace(returncode=0, stdout="state=active used_memory=1048576", stderr="")
        return SimpleNamespace(returncode=0, stdout="applied", stderr="")

    app.dependency_overrides[get_db] = get_test_db
    monkeypatch.setattr(auth_api, "_enforce_rate_limit", lambda key: None)
    monkeypatch.setattr(site_users, "ensure_panel_user", lambda *a, **k: None)
    monkeypatch.setattr(resource_limits, "sync_in_background", lambda: None)
    monkeypatch.setattr(redis_user.shell, "privileged", privileged)
    monkeypatch.setattr(redis_user, "installed", lambda: True)
    monkeypatch.setattr(redis_user, "STOPPED_MARKER", tmp_path / "stopped")
    plan_syncs = []
    monkeypatch.setattr(redis_user, "sync_plan_in_background", plan_syncs.append)
    client = TestClient(app)
    try:
        yield SimpleNamespace(db=db, client=client, calls=calls, plan_syncs=plan_syncs, tmp_path=tmp_path)
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _login(client, name):
    client.cookies.clear()
    assert client.post("/api/auth/login", data={"username": name, "password": PASSWORD}).status_code == 200


def _call(client, method, path, **kwargs):
    return client.request(method, path, headers={"X-CSRF-Token": client.cookies.get("opanel_csrf", "")}, **kwargs)


def _user(db, name):
    db.expire_all()
    return db.query(User).filter(User.username == name).one()


def _plan(db, name, mb):
    plan = HostingPlan(slug=name, name=name, redis_memory_mb=mb)
    db.add(plan)
    db.commit()
    return plan


def test_the_limit_is_the_packages_else_the_accounts_own(env):
    db = env.db
    user = _user(db, "direct")
    assert user.redis_memory_mb == 64 and user.redis_enabled is False, "64 MB, off, by default"
    assert redis_user.limit_mb(db, user) == 64
    user.redis_memory_mb = 0
    assert redis_user.limit_mb(db, user) == 0
    plan = _plan(db, "pro", 256)
    user.plan_id = plan.id
    assert redis_user.limit_mb(db, user) == 256, "a package's value wins"
    plan.redis_memory_mb = 0
    assert redis_user.limit_mb(db, user) == 0
    user.plan_id = 9999
    assert redis_user.limit_mb(db, user) == 0, "a package that is gone leaves the account's own"
    user.plan_id, user.redis_memory_mb = None, 99999
    assert redis_user.limit_mb(db, user) == 4096


def test_an_account_turns_its_own_redis_on_and_off(env):
    _login(env.client, "direct")
    info = _call(env.client, "GET", "/api/redis").json()
    assert info["installed"] and info["allowed"] and info["limit_mb"] == 64 and not info["enabled"]
    assert info["socket"] == "/home/direct/.redis/redis.sock"

    answer = _call(env.client, "POST", "/api/redis/enable")
    assert answer.status_code == 200, answer.text
    user = _user(env.db, "direct")
    assert user.redis_enabled
    assert env.calls[0] == ["redis-user-apply", "direct", "64", resource_limits.account_slice(user)]
    assert answer.json()["running"] is True and answer.json()["used_bytes"] == 1048576

    assert _call(env.client, "POST", "/api/redis/flush").status_code == 200
    assert ["redis-user-flush", "direct"] in env.calls
    assert _call(env.client, "POST", "/api/redis/disable").status_code == 200
    assert ["redis-user-remove", "direct"] in env.calls
    assert not _user(env.db, "direct").redis_enabled


def test_turning_it_on_is_refused_when_it_may_not_run(env, monkeypatch):
    db = env.db
    _login(env.client, "direct")
    user = _user(db, "direct")
    user.redis_memory_mb = 0
    db.commit()
    refused = _call(env.client, "POST", "/api/redis/enable")
    assert refused.status_code == 400 and "package" in refused.json()["detail"]
    assert _call(env.client, "GET", "/api/redis").json()["allowed"] is False

    user = _user(db, "direct")
    user.redis_memory_mb = 64
    db.commit()
    (env.tmp_path / "stopped").write_text("", encoding="utf-8")
    assert _call(env.client, "POST", "/api/redis/enable").status_code == 400
    (env.tmp_path / "stopped").unlink()

    monkeypatch.setattr(redis_user, "installed", lambda: False)
    assert _call(env.client, "POST", "/api/redis/enable").status_code == 400
    assert _call(env.client, "GET", "/api/redis").json()["installed"] is False
    monkeypatch.setattr(redis_user, "installed", lambda: True)

    # Suspended (an administrator logged in as the account still sees it).
    user = _user(db, "direct")
    user.is_active = False
    db.commit()
    with pytest.raises(ValueError, match="suspended"):
        redis_user.enable(db, user)
    assert env.calls == [], "nothing reached the helper"


def test_an_administrator_has_none_of_its_own(env):
    _login(env.client, "root_admin")
    info = _call(env.client, "GET", "/api/redis").json()
    assert info["allowed"] is False and info["problem"] == "admin" and info["detail"]
    assert _call(env.client, "POST", "/api/redis/enable").status_code == 400
    assert env.calls == []
    direct = _user(env.db, "direct")
    direct.redis_enabled = True
    env.db.commit()
    listed = _call(env.client, "GET", "/api/redis/accounts").json()
    assert [row["username"] for row in listed["accounts"]] == ["direct"]
    _login(env.client, "direct")
    assert _call(env.client, "GET", "/api/redis/accounts").status_code == 403


def test_a_demo_account_cannot_change_it(monkeypatch):
    monkeypatch.setattr(demo_mode, "is_demo_account", lambda user: True)
    monkeypatch.setattr(demo_mode, "is_active", lambda: True)

    def request(method, path):
        return SimpleNamespace(method=method, url=SimpleNamespace(path=path), state=SimpleNamespace())

    demo_mode.enforce(request("GET", "/api/redis"), object())
    for path in ("/api/redis/enable", "/api/redis/disable", "/api/redis/flush"):
        with pytest.raises(Exception) as refused:
            demo_mode.enforce(request("POST", path), object())
        assert getattr(refused.value, "status_code", None) == 403


def test_the_memory_is_set_on_packages_and_accounts(env):
    client = env.client
    _login(client, "root_admin")
    made = _call(client, "POST", "/api/plans", json={"slug": "pro", "name": "Pro", "redis_memory_mb": 128})
    assert made.status_code == 200, made.text
    assert made.json()["redis_memory_mb"] == 128
    assert _call(client, "POST", "/api/plans", json={"slug": "big", "name": "Big", "redis_memory_mb": 5000}).status_code == 422
    assert _call(client, "POST", "/api/plans", json={"slug": "dflt", "name": "Default"}).json()["redis_memory_mb"] == 64
    account = _call(client, "POST", "/api/users", json={"username": "cust1", **ACCOUNT, "redis_memory_mb": 0})
    assert account.status_code == 200, account.text
    assert account.json()["redis_memory_mb"] == 0 and account.json()["redis_enabled"] is False
    assert _call(client, "PATCH", f"/api/users/{account.json()['id']}", json={"redis_memory_mb": -1}).status_code == 422


def test_a_package_edit_reaches_its_accounts(env):
    db, client = env.db, env.client
    plan = _plan(db, "pro", 128)
    user = _user(db, "direct")
    user.plan_id, user.redis_enabled = plan.id, True
    db.commit()
    _login(client, "root_admin")
    assert _call(client, "PATCH", f"/api/plans/{plan.id}", json={"name": "Pro 2"}).status_code == 200
    assert env.plan_syncs == [], "nothing to do when the memory did not change"
    assert _call(client, "PATCH", f"/api/plans/{plan.id}", json={"redis_memory_mb": 256}).status_code == 200
    assert env.plan_syncs == [plan.id]

    db.expire_all()
    redis_user.sync_plan(db, plan.id)
    assert env.calls[-1][:3] == ["redis-user-apply", "direct", "256"]

    plan = db.query(HostingPlan).filter(HostingPlan.id == plan.id).one()
    plan.redis_memory_mb = 0
    db.commit()
    redis_user.sync_plan(db, plan.id)
    assert env.calls[-1] == ["redis-user-remove", "direct"]
    assert not _user(db, "direct").redis_enabled, "0 MB turns it off for good"


def test_a_package_switch_or_a_custom_limit_re_applies(env):
    db, client = env.db, env.client
    plan = _plan(db, "pro", 512)
    user = _user(db, "direct")
    user.redis_enabled = True
    db.commit()
    _login(client, "root_admin")
    assert _call(client, "PATCH", f"/api/users/{user.id}", json={"plan_id": plan.id}).status_code == 200
    assert env.calls[-1][:3] == ["redis-user-apply", "direct", "512"]
    assert _call(client, "PATCH", f"/api/users/{user.id}", json={"plan_id": 0, "redis_memory_mb": 32}).status_code == 200
    assert env.calls[-1][:3] == ["redis-user-apply", "direct", "32"]
    assert _call(client, "PATCH", f"/api/users/{user.id}", json={"redis_memory_mb": 0}).status_code == 200
    assert env.calls[-1] == ["redis-user-remove", "direct"]
    assert not _user(db, "direct").redis_enabled


def test_suspension_stops_it_and_keeps_the_switch(env):
    db, client = env.db, env.client
    user = _user(db, "direct")
    user.redis_enabled = True
    db.commit()
    _login(client, "root_admin")
    assert _call(client, "PATCH", f"/api/users/{user.id}", json={"is_active": False}).status_code == 200
    assert env.calls[-1] == ["redis-user-remove", "direct"]
    assert _user(db, "direct").redis_enabled
    assert _call(client, "PATCH", f"/api/users/{user.id}", json={"is_active": True}).status_code == 200
    assert env.calls[-1][:3] == ["redis-user-apply", "direct", "64"]


def test_nothing_reaches_the_helper_for_an_account_with_it_off(env):
    _login(env.client, "root_admin")
    user = _user(env.db, "direct")
    assert _call(env.client, "PATCH", f"/api/users/{user.id}", json={"redis_memory_mb": 16}).status_code == 200
    assert env.calls == []


def test_deleting_an_account_removes_its_redis(env, monkeypatch):
    monkeypatch.setattr(site_users, "delete_panel_user", lambda *a, **k: None)
    user = _user(env.db, "direct")
    user.redis_enabled = True
    env.db.commit()
    _login(env.client, "root_admin")
    answer = _call(env.client, "DELETE", f"/api/users/{user.id}")
    assert answer.status_code == 200, answer.text
    assert ["redis-user-remove", "direct"] in env.calls


def test_deleting_a_package_leaves_its_accounts_their_memory(env):
    db, client = env.db, env.client
    plan = _plan(db, "pro", 300)
    user = _user(db, "direct")
    user.plan_id = plan.id
    db.commit()
    _login(client, "root_admin")
    assert _call(client, "DELETE", f"/api/plans/{plan.id}").status_code == 200
    user = _user(db, "direct")
    assert user.redis_memory_mb == 300 and redis_user.limit_mb(db, user) == 300


def test_removing_the_addon_turns_every_account_off(env):
    db = env.db
    for name in ("direct", "root_admin"):
        _user(db, name).redis_enabled = True
    db.commit()
    redis_user.forget_all(db)
    assert not any(user.redis_enabled for user in db.query(User).all())
