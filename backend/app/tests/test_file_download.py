"""File-manager and backup downloads, as the browser's download manager sees them.

A 580 MB zip on .122 never arrived: the page pulled it through fetch + blob,
the API gzipped it on the fly (no Content-Length, no progress), and a reload
or a second click lost it. Downloads now go to the browser itself, so the
response has to carry its real size, a filename that always encodes, and
byte ranges so an interrupted download resumes.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import maintenance
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import User, Website

BODY = bytes(range(256)) * 4096  # 1 MiB of incompressible-looking bytes


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    admin = User(username="admin", email="admin@example.test", role="admin", is_active=True,
                 hashed_password=hash_password("PasswordLongEnough1"))
    db.add(admin)
    db.commit()
    site = Website(domain="tap.test", owner_id=admin.id, root_path=str(tmp_path), document_root="public_html",
                   linux_user="admin", php_version="8.3", app_type="php", status="active")
    db.add(site)
    db.commit()

    # requested path -> (name on disk, bytes). The quote cannot be in a Windows
    # file name, so the tricky name is only the one the download reports.
    files = {"public_html/site.zip": ("site.zip", BODY),
             "public_html/tài liệu \"cũ\".pdf": ("doc.pdf", b"%PDF-1.7 hello")}
    (tmp_path / "public_html").mkdir()
    for disk_name, data in files.values():
        (tmp_path / "public_html" / disk_name).write_bytes(data)
    opened = []

    def fake_handle(website, relative_path, allow_sensitive=False):
        if relative_path not in files:
            raise ValueError("File not found")
        disk_name, data = files[relative_path]
        handle = (Path(website.root_path) / "public_html" / disk_name).open("rb")
        opened.append(handle)
        return handle, Path(relative_path).name, len(data)

    monkeypatch.setattr(maintenance.file_manager, "download_file_handle", fake_handle)

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, admin.id)
    client = TestClient(app)

    def get(path, **headers):
        return client.get(f"/api/maintenance/files/{site.id}/download", params={"path": path}, headers=headers)

    try:
        yield SimpleNamespace(client=client, get=get, site=site, opened=opened, tmp=tmp_path)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_a_download_carries_its_real_size_and_is_never_gzipped(env):
    res = env.get("public_html/site.zip", **{"Accept-Encoding": "gzip, deflate, br"})
    assert res.status_code == 200
    assert "content-encoding" not in res.headers
    assert res.headers["content-length"] == str(len(BODY))
    assert res.headers["accept-ranges"] == "bytes"
    assert res.headers["etag"] and res.headers["last-modified"]
    assert res.headers["content-disposition"] == 'attachment; filename="site.zip"'
    assert res.content == BODY
    assert all(handle.closed for handle in env.opened)


def test_an_interrupted_download_resumes_from_where_it_stopped(env):
    etag = env.get("public_html/site.zip").headers["etag"]
    res = env.get("public_html/site.zip", Range="bytes=1000-", **{"If-Range": etag})
    assert res.status_code == 206
    assert res.headers["content-range"] == f"bytes 1000-{len(BODY) - 1}/{len(BODY)}"
    assert res.headers["content-length"] == str(len(BODY) - 1000)
    assert res.content == BODY[1000:]


def test_a_closed_range_and_a_suffix_range_return_just_those_bytes(env):
    res = env.get("public_html/site.zip", Range="bytes=10-19")
    assert res.status_code == 206 and res.content == BODY[10:20]
    res = env.get("public_html/site.zip", Range="bytes=-16")
    assert res.status_code == 206 and res.content == BODY[-16:]


def test_a_resume_against_a_changed_file_starts_over(env):
    res = env.get("public_html/site.zip", Range="bytes=1000-", **{"If-Range": '"stale-etag"'})
    assert res.status_code == 200
    assert res.content == BODY


def test_a_range_past_the_end_is_refused_with_the_size(env):
    res = env.get("public_html/site.zip", Range=f"bytes={len(BODY)}-")
    assert res.status_code == 416
    assert res.headers["content-range"] == f"bytes */{len(BODY)}"
    assert all(handle.closed for handle in env.opened)


def test_several_ranges_get_the_whole_file(env):
    res = env.get("public_html/site.zip", Range="bytes=0-1,5-6")
    assert res.status_code == 200 and res.content == BODY


def test_a_vietnamese_file_name_downloads(env):
    # A name outside Latin-1 used to be put in the header as-is and could not
    # be encoded, so the download died with a 500.
    res = env.get('public_html/tài liệu "cũ".pdf')
    assert res.status_code == 200
    assert res.headers["content-disposition"] == (
        "attachment; filename*=utf-8''t%C3%A0i%20li%E1%BB%87u%20%22c%C5%A9%22.pdf"
    )
    assert res.content == b"%PDF-1.7 hello"


def test_check_answers_with_name_and_size_and_sends_no_file(env):
    res = env.client.get(f"/api/maintenance/files/{env.site.id}/download",
                         params={"path": "public_html/site.zip", "check": 1})
    assert res.status_code == 200
    assert res.json() == {"name": "site.zip", "size": len(BODY)}
    assert all(handle.closed for handle in env.opened)


def test_check_reports_why_a_download_is_refused(env):
    res = env.client.get(f"/api/maintenance/files/{env.site.id}/download",
                         params={"path": "public_html/missing.zip", "check": 1})
    assert res.status_code == 400
    assert res.json()["detail"] == "File not found"


def test_backup_downloads_can_be_checked_first(env, monkeypatch):
    archive = env.tmp / "tap.test-2026.tar.gz"
    archive.write_bytes(b"\x1f\x8b" + b"x" * 98)
    monkeypatch.setattr(maintenance.backup, "backup_path", lambda domain, name: archive)
    monkeypatch.setattr(maintenance.backup, "user_backup_path", lambda name: archive)

    res = env.client.get(f"/api/maintenance/backups/{env.site.id}/download",
                         params={"backup_file": archive.name, "check": 1})
    assert res.json() == {"name": archive.name, "size": 100}
    res = env.client.get("/api/maintenance/user-backups-download",
                         params={"backup_file": archive.name, "check": 1})
    assert res.json() == {"name": archive.name, "size": 100}

    res = env.client.get(f"/api/maintenance/backups/{env.site.id}/download",
                         params={"backup_file": archive.name}, headers={"Range": "bytes=2-"})
    assert res.status_code == 206 and res.content == b"x" * 98


def test_the_page_hands_downloads_to_the_browser():
    source = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    for name in ("downloadFile", "downloadBackup", "downloadUserBackup"):
        start = source.index(f"async function {name}(")
        body = source[start:source.index("\n  }\n", start)]
        assert "browserDownload(" in body, name
        assert "blob()" not in body, name
