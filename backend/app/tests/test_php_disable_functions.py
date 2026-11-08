"""Every account's PHP disables the dangerous functions until its owner takes
one off (operator, 2026-11-08): exec, system, passthru, shell_exec, proc_open...

Per account: LiteSpeed runs one lsphp per Linux user for all of its sites, and
lsphp ignores disable_functions in a vhost's phpIniOverride (tested on .41),
so the helper writes it into the account's root-owned PHP ini directory - the
default list unless the account has its own - and log-hygiene brings it to
every account already on the box.
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import websites as api
from app.core.database import Base
from app.models.entities import User, Website
from app.services import php_functions

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
PAGE = (ROOT / "frontend" / "src" / "pages" / "WebsiteDetail.jsx").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = HELPER.index(f"\n{name}() {{\n") + 1
    return HELPER[start:HELPER.index("\n}\n", start)]


def test_one_default_list_in_the_helper_the_service_and_the_page():
    helper = re.search(r'^PHP_DEFAULT_DISABLE_FUNCTIONS="([^"]+)"$', HELPER, re.M).group(1).split(",")
    block = PAGE[PAGE.index("const PHP_DEFAULT_DISABLED = ["):]
    page = re.findall(r"'([a-z_]+)'", block[:block.index("];")])
    assert helper == list(php_functions.DEFAULT_DISABLED) == page


def test_the_account_ini_carries_the_list_and_restarts_its_php():
    ini = _function("ensure_php_disable_ini")
    assert 'ini="${ini_dir}/50-opanel-disable-functions.ini"' in ini
    assert "printf 'disable_functions = %s\\n' \"$list\"" in ini
    assert 'pkill -x lsphp -u "$user"' in ini, "-x, so it never matches a shell that mentions lsphp"
    assert ini.index("cmp -s") < ini.index("pkill"), "only when the ini changed"
    listing = _function("user_php_disable_list")
    assert listing.count("PHP_DEFAULT_DISABLE_FUNCTIONS") == 2


def test_every_account_gets_it_new_ones_and_existing_ones():
    assert 'ensure_php_disable_ini "$user" 2>/dev/null || true' in _function("ensure_php_file_cache_all")
    runtime = HELPER[HELPER.index('  ensure_php_file_cache "$user"\n  ensure_php_disable_ini "$user"\n}'):]
    assert runtime
    assert 'rm -f "${PHP_DISABLE_ROOT}/${user}" 2>/dev/null || true' in HELPER


def test_the_helper_takes_only_a_list_of_names():
    assert '^[a-z_][a-z0-9_]*(,[a-z_][a-z0-9_]*)*$' in _function("require_php_function_list")
    verb = HELPER[HELPER.index("  user-php-disable)\n"):]
    assert 'set_user_php_disable "$1" "$2"' in verb[:verb.index("\n    ;;\n")]


@pytest.mark.parametrize("text, expected", [(None, None), ("", ""), ("exec, system\nEXEC,popen", "exec,system,popen")])
def test_normalize(text, expected):
    assert php_functions.normalize(text) == expected


@pytest.mark.parametrize("text", ["exec;rm -rf /", "a,b c$", "1abc"])
def test_normalize_refuses_what_is_not_a_function_name(text):
    with pytest.raises(ValueError):
        php_functions.normalize(text)


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    people = {}
    for name in ("alice", "bob"):
        people[name] = User(username=name, email=f"{name}@example.test", role="end_user", is_active=True,
                            hashed_password="x", token_version=0)
        db.add(people[name])
    db.commit()
    site = Website(domain="shop.test", owner_id=people["alice"].id, root_path="/home/alice/shop.test",
                   linux_user="alice", app_type="wordpress", php_version="8.4", status="active", ssl_enabled=False)
    db.add(site)
    db.commit()
    calls = []
    monkeypatch.setattr(php_functions.shell, "privileged", lambda verb, helper_args=None, **k: calls.append((verb, *helper_args)))
    monkeypatch.setattr(api, "log_action", lambda *a, **k: None)
    yield db, people, site, calls
    db.close()


def _put(env, who, functions):
    db, people, site, _ = env
    return api.set_website_disable_functions(site.id, api.WebsiteDisableFunctions(functions=functions),
                                             request=SimpleNamespace(client=None, headers={}), db=db, current_user=people[who])


def test_the_owner_takes_a_function_off_and_goes_back_to_the_default(env):
    _, people, site, calls = env
    assert site.disable_functions is None
    assert _put(env, "alice", "system,passthru").disable_functions == "system,passthru"
    assert calls[-1] == ("user-php-disable", "alice", "system,passthru")
    assert people["alice"].php_disable_functions == "system,passthru"
    assert _put(env, "alice", "").disable_functions == "" and calls[-1][-1] == "none"
    assert _put(env, "alice", None).disable_functions is None and calls[-1][-1] == "default"


def test_another_customer_cannot(env):
    _, _, _, calls = env
    with pytest.raises(HTTPException) as err:
        _put(env, "bob", "")
    assert err.value.status_code in (403, 404) and calls == []
