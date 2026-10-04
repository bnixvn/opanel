"""`opanel sync-admin-root-password` asks nothing.

It asked for the root password to put in /root/login.txt; the operator wants
the command to just do it (2026-10-05). Only root's hash is readable, so
login.txt now says the admin password is the root password.
"""
from __future__ import annotations

from pathlib import Path

CTL = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanelctl").read_text(encoding="utf-8")


def _body() -> str:
    body = CTL[CTL.index("sync_admin_root_password() {"):]
    return body[:body.index("\n}\n")]


def test_it_asks_nothing_and_updates_login_txt():
    body = _body()
    assert "read " not in body, "the command must not stop to ask"
    assert 'write_login_info "same as the root password"' in body


def test_usermod_runs_only_when_the_hash_changes():
    body = _body()
    guard = body.index('!= "$root_hash" ]]; then')
    assert guard < body.index('usermod -p "$root_hash" admin')


def test_the_command_and_menu_entry_stay():
    assert "sync-admin-root-password|--sync-admin-root-password" in CTL
    assert "12) sync_admin_root_password ;;" in CTL
