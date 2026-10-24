"""The panel's release notes, read from the releases of its own repository.

The Updates page shows what the releases after the installed one change, so
an administrator can read an update before running it, and the whole history
on a page of its own. The notes are written once, for the GitLab (and GitHub)
release; the panel reads them from there instead of carrying a second copy
that would drift.

Fetched at most once an hour and kept on disk, so a page view never waits on
the network and an unreachable GitLab still shows the last list it gave.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core.version import APP_VERSION
from app.services import updates

CACHE_FILE = updates.UPDATE_STATE_FILE.parent / "changelog.json"
CACHE_SECONDS = 3600
# Enough for years of releases; a page more is not worth a second request.
FETCH_LIMIT = 100
NOTES_LIMIT = 30000
_TAG_RE = re.compile(r"^v?([0-9]+(?:\.[0-9]+)*)$")


def releases_api(repo_url: str) -> str:
    """The releases API of the repository the panel updates from, or ""."""
    url = (repo_url or "").strip()
    if url.startswith("git@"):
        host, _, path = url[4:].partition(":")
        url = f"https://{host}/{path}"
    if not url.startswith("https://"):
        return ""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.strip("/")
    path = path[:-4] if path.endswith(".git") else path
    if not path or "/" not in path:
        return ""
    if parsed.netloc == "github.com":
        return f"https://api.github.com/repos/{path}/releases?per_page={FETCH_LIMIT}"
    return (f"https://{parsed.netloc}/api/v4/projects/{urllib.parse.quote(path, safe='')}"
            f"/releases?per_page={FETCH_LIMIT}")


def _version_tuple(text: str) -> tuple:
    """"1.10.0" above "1.9.8", which comparing strings gets backwards."""
    return tuple(int(part) for part in re.findall(r"[0-9]+", text or "")[:4])


def _version(tag: str) -> str:
    match = _TAG_RE.match((tag or "").strip())
    return match.group(1) if match else ""


def _normalise(rows: list) -> list[dict]:
    """GitLab and GitHub rows as one shape, newest first, versions only."""
    releases = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("draft") or row.get("prerelease"):
            continue
        version = _version(row.get("tag_name", ""))
        if not version:
            continue
        notes = row.get("description")
        if notes is None:
            notes = row.get("body")
        link = row.get("html_url") or ((row.get("_links") or {}).get("self")) or ""
        releases.append({
            "version": version,
            "name": str(row.get("name") or f"v{version}")[:200],
            "date": str(row.get("released_at") or row.get("published_at") or row.get("created_at") or "")[:25],
            "notes": str(notes or "")[:NOTES_LIMIT],
            "url": link if str(link).startswith("https://") else "",
        })
    releases.sort(key=lambda item: _version_tuple(item["version"]), reverse=True)
    return releases


def _fetch() -> list[dict]:
    api = releases_api(updates.REPO_URL)
    if not api:
        raise RuntimeError(f"No releases can be read for {updates.REPO_URL}")
    request = urllib.request.Request(api, headers={"Accept": "application/json", "User-Agent": "panel-changelog"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return _normalise(json.loads(response.read().decode("utf-8")))


def _read_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(data: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def releases(refresh: bool = False) -> tuple[list[dict], str, str]:
    """(releases, fetched_at, error). The cache answers when the fetch fails."""
    cached = _read_cache()
    fresh = time.time() - float(cached.get("fetched_ts") or 0) < CACHE_SECONDS
    if cached.get("releases") is not None and fresh and not refresh:
        return cached["releases"], cached.get("fetched_at", ""), ""
    try:
        rows = _fetch()
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        return cached.get("releases") or [], cached.get("fetched_at", ""), f"Could not read the releases: {exc}"
    data = {"releases": rows, "fetched_ts": time.time(), "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _write_cache(data)
    return rows, data["fetched_at"], ""


def page(page: int = 1, per_page: int = 10, newer_only: bool = False, refresh: bool = False) -> dict:
    rows, fetched_at, error = releases(refresh)
    current = _version_tuple(APP_VERSION)
    marked = [{**row, "newer": _version_tuple(row["version"]) > current,
               "current": row["version"] == APP_VERSION} for row in rows]
    newer_count = sum(1 for row in marked if row["newer"])
    if newer_only:
        marked = [row for row in marked if row["newer"]]
    per_page = max(1, min(int(per_page or 10), 50))
    page = max(1, int(page or 1))
    start = (page - 1) * per_page
    return {
        "current_version": APP_VERSION,
        "releases": marked[start:start + per_page],
        "total": len(marked),
        "page": page,
        "per_page": per_page,
        "newer_count": newer_count,
        "fetched_at": fetched_at,
        "error": error,
    }
