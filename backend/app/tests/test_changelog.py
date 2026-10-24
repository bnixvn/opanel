"""The Updates page's changelog: the panel's release notes, read from the
releases of the repository it updates from."""
from pathlib import Path

import pytest

from app.services import changelog

FRONTEND = Path(__file__).resolve().parents[3] / "frontend" / "src"

GITLAB_ROWS = [
    {"tag_name": "v1.2.0", "name": "Panel 1.2.0", "description": "## 1.2.0\n- new", "released_at": "2026-10-20T10:00:00Z",
     "_links": {"self": "https://gitlab.example/g/p/-/releases/v1.2.0"}},
    {"tag_name": "v1.10.0", "name": "Panel 1.10.0", "description": "ten", "released_at": "2026-10-24T10:00:00Z"},
    {"tag_name": "v1.1.0", "name": "Panel 1.1.0", "description": "one", "released_at": "2026-10-01T10:00:00Z"},
    {"tag_name": "nightly", "name": "not a version", "description": "x"},
]


@pytest.fixture
def notes(tmp_path, monkeypatch):
    monkeypatch.setattr(changelog, "CACHE_FILE", tmp_path / "changelog.json")
    monkeypatch.setattr(changelog, "APP_VERSION", "1.2.0")
    fetched = {"rows": GITLAB_ROWS, "calls": 0, "fail": False}

    def fetch():
        fetched["calls"] += 1
        if fetched["fail"]:
            raise OSError("network down")
        return changelog._normalise(fetched["rows"])

    monkeypatch.setattr(changelog, "_fetch", fetch)
    return fetched


def test_the_releases_api_of_the_update_repository():
    assert changelog.releases_api("https://gitlab.sieunhim.com/bnix/opanel.git") == \
        "https://gitlab.sieunhim.com/api/v4/projects/bnix%2Fopanel/releases?per_page=100"
    assert changelog.releases_api("git@gitlab.sieunhim.com:bnix/opanel.git") == \
        "https://gitlab.sieunhim.com/api/v4/projects/bnix%2Fopanel/releases?per_page=100"
    assert changelog.releases_api("https://github.com/bnixvn/opanel.git") == \
        "https://api.github.com/repos/bnixvn/opanel/releases?per_page=100"
    assert changelog.releases_api("file:///srv/opanel") == ""


def test_newest_first_by_version_and_only_versions(notes):
    data = changelog.page()
    assert [row["version"] for row in data["releases"]] == ["1.10.0", "1.2.0", "1.1.0"]
    assert data["releases"][1]["url"] == "https://gitlab.example/g/p/-/releases/v1.2.0"
    assert data["releases"][1]["notes"].startswith("## 1.2.0")


def test_what_the_update_brings_is_marked(notes):
    data = changelog.page()
    by_version = {row["version"]: row for row in data["releases"]}
    assert by_version["1.10.0"]["newer"] and not by_version["1.2.0"]["newer"]
    assert by_version["1.2.0"]["current"]
    assert data["newer_count"] == 1
    assert [row["version"] for row in changelog.page(newer_only=True)["releases"]] == ["1.10.0"]


def test_paged_on_the_server(notes):
    data = changelog.page(page=2, per_page=2)
    assert [row["version"] for row in data["releases"]] == ["1.1.0"]
    assert data["total"] == 3 and data["per_page"] == 2


def test_read_once_an_hour_and_the_last_list_survives_an_outage(notes):
    changelog.page()
    changelog.page()
    assert notes["calls"] == 1
    notes["fail"] = True
    data = changelog.page(refresh=True)
    assert notes["calls"] == 2
    assert data["total"] == 3 and "network down" in data["error"]


def test_github_rows_read_the_same(notes):
    rows = changelog._normalise([{"tag_name": "v2.0.0", "name": "", "body": "**b**", "published_at": "2026-10-24T00:00:00Z",
                                  "html_url": "https://github.com/o/r/releases/tag/v2.0.0"},
                                 {"tag_name": "v2.1.0", "draft": True, "body": "draft"}])
    assert rows == [{"version": "2.0.0", "name": "v2.0.0", "date": "2026-10-24T00:00:00Z", "notes": "**b**",
                     "url": "https://github.com/o/r/releases/tag/v2.0.0"}]


def test_notes_are_drawn_without_html():
    markdown = (FRONTEND / "components" / "Markdown.jsx").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in markdown
    assert "https:\\/\\/" in markdown  # links only to https
