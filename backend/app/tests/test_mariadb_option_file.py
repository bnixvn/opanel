"""The scoped import's password reads back from its option file unchanged (1.49.3).

import_database(as_user=..., as_password=...) hands mysql a 0600 [client]
defaults-file. The password went in unquoted, and a MariaDB option file cuts an
unquoted value at the first `#` - which random_password() puts in about one
password in three. Those imports were refused with ERROR 1045. Found on a BPanel
box running the same code against a real MariaDB (2026-11-20).
"""
import subprocess
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import mariadb


@pytest.fixture
def mysql(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "command_dry_run", False)
    calls = []

    def fake_run(args, stdin=None, capture_output=False, check=False):
        defaults = next(a.split("=", 1)[1] for a in args if a.startswith("--defaults-file="))
        calls.append({"args": args, "defaults": Path(defaults).read_text(encoding="utf-8")})
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(mariadb.subprocess, "run", fake_run)
    dump = tmp_path / "restore.sql"
    dump.write_text("SELECT 1;\n", encoding="utf-8")
    return calls, str(dump)


@pytest.mark.parametrize("password, line", [
    ("plain", 'password="plain"'),
    ("abc#def", 'password="abc#def"'),
    ("a;b c", 'password="a;b c"'),
    ("back\\slash", 'password="back\\\\slash"'),
    ('say "hi"', "password='say \"hi\"'"),
])
def test_the_password_reads_back_as_it_is(mysql, password, line):
    calls, dump = mysql
    mariadb.import_database("shop_db", dump, as_user="shop_user", as_password=password)
    (call,) = calls
    assert line + "\n" in call["defaults"] and "user=shop_user\n" in call["defaults"]
    # Never in argv, and the file is gone afterwards.
    assert not any(password in arg for arg in call["args"])
    assert not Path(call["args"][1].split("=", 1)[1]).exists()


def test_every_password_random_password_makes_survives():
    for _ in range(200):
        password = mariadb.random_password()
        assert mariadb._option_value(password) == f'"{password}"'


@pytest.mark.parametrize("password", ["it's \"both\"", "line\nbreak", "nul\x00"])
def test_a_password_no_option_file_can_hold_is_refused(mysql, password):
    _calls, dump = mysql
    with pytest.raises(ValueError):
        mariadb.import_database("shop_db", dump, as_user="shop_user", as_password=password)
