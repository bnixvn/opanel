"""Paste in another website of the same account (operator, 2026-11-08, as
BPanel): copied in one site, Paste in another was refused. Never into a site
another Linux user runs."""
from types import SimpleNamespace

import pytest

from app.api import maintenance
from app.services import file_manager


def _site(tmp_path, name, user=None):
    root = tmp_path / name
    (root / "public_html").mkdir(parents=True)
    return SimpleNamespace(root_path=str(root), linux_user=user, domain=name)


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr(file_manager, "_clear_fastcgi_cache", lambda: None)
    monkeypatch.setattr(file_manager.site_users, "fix_site_path", lambda *a, **k: None)


def test_copy_and_move_land_in_the_other_site(tmp_path):
    a, b = _site(tmp_path, "a.test"), _site(tmp_path, "b.test")
    (tmp_path / "a.test/public_html/page.html").write_text("hi", encoding="utf-8")
    file_manager.copy_entries(a, ["public_html/page.html"], "public_html", into=b)
    assert (tmp_path / "b.test/public_html/page.html").read_text(encoding="utf-8") == "hi"
    assert (tmp_path / "a.test/public_html/page.html").exists()
    file_manager.move_entries(a, ["public_html/page.html"], "", into=b)
    assert (tmp_path / "b.test/page.html").exists() and not (tmp_path / "a.test/public_html/page.html").exists()


def test_never_into_another_linux_users_site(tmp_path):
    a, b = _site(tmp_path, "a.test", "alice"), _site(tmp_path, "b.test", "bob")
    (tmp_path / "a.test/public_html/page.html").write_text("hi", encoding="utf-8")
    for transfer in (file_manager.copy_entries, file_manager.move_entries):
        with pytest.raises(ValueError, match="same account"):
            transfer(a, ["public_html/page.html"], "public_html", into=b)


def test_the_helper_gets_absolute_paths_only_across_sites(tmp_path, monkeypatch):
    a, b = _site(tmp_path, "a.test", "alice"), _site(tmp_path, "b.test", "alice")
    (tmp_path / "a.test/public_html/page.html").write_text("hi", encoding="utf-8")
    calls = []
    monkeypatch.setattr(file_manager, "_run_as_site_user", lambda site, cwd, cmd, args, fallback=None: calls.append(args))
    file_manager.copy_entries(a, ["public_html/page.html"], "")
    file_manager.copy_entries(a, ["public_html/page.html"], "public_html", into=b)
    assert calls[0][-2:] == ["public_html/page.html", "page.html"]
    assert calls[1][-1] == str((tmp_path / "b.test/public_html/page.html").resolve())


def test_the_destination_site_is_checked_like_the_source(monkeypatch):
    seen = []
    monkeypatch.setattr(maintenance, "get_owned_website", lambda db, user, website_id: seen.append(website_id) or "W")
    assert maintenance._transfer_target(None, None, maintenance.FileTransfer(website_id=1, paths=["a"], destination_website_id=1)) is None
    assert maintenance._transfer_target(None, None, maintenance.FileTransfer(website_id=1, paths=["a"], destination_website_id=2)) == "W"
    assert seen == [2]
