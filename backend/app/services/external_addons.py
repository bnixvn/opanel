"""Addons whose code lives in a repository of their own (operator, 2026-11-16:
"làm addon nhưng nằm ở gitlab độc lập. Cần dùng thì chạy lệnh cài vào").

Which ones exist is still decided here, in the panel's repository: KNOWN
names each one and the GitLab project it comes from, so nothing outside this
repository can name an addon into the panel. `opanel addon install <id>`
(installer/files/opanelctl) fetches the project's latest release as root into
ROOT/<id>, owned by root and writable by nobody else; this module loads it
when the API starts - after checking that every file of it still is root's
and not writable by the panel user - and hands it the narrow API in
PanelApi. A file the panel user could have written is never imported.

An addon's manifest (addon.json) says what it is:
    {"id": "gdrive", "name": "...", "version": "1.0.0", "panel_api": 1,
     "module": "gdrive"}
and its module has ``register(panel)``.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("opanel.external_addons")

ROOT = Path(os.environ.get("OPANEL_EXTERNAL_ADDONS", "/opt/opanel-addons"))
# The version of PanelApi an addon is written against; one written for
# another is not loaded.
PANEL_API = 1

KNOWN: dict[str, dict] = {
    "gdrive": {
        "name": "Google Drive backup",
        "project": "bnix/addon-gdrive",
        "summary": "Backups to Google Drive: a backup destination like SFTP and S3, signed in with a code at google.com/device.",
        "details": [
            "Adds Google Drive to Backups > Destinations; schedules, Run now and Restore use it like any other destination.",
            "The panel only ever sees the files it puts in Drive itself (Google's drive.file permission).",
            "Lives in a repository of its own and is installed from the server's shell.",
        ],
    },
}

_loaded: dict[str, dict] = {}


class AddonError(RuntimeError):
    """An external addon that is missing, broken or not to be trusted."""


def manifest(slug: str) -> dict:
    """The installed addon's addon.json, or {} when it is not on disk."""
    try:
        data = json.loads((ROOT / slug / "addon.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def catalogue_entries() -> dict[str, dict]:
    """KNOWN as entries of addons.ADDONS: shell only, no root helper, the
    version the one on disk."""
    entries = {}
    for slug, entry in KNOWN.items():
        entries[slug] = {
            "id": slug,
            "name": entry["name"],
            "summary": entry["summary"],
            "description": " ".join(entry["details"]),
            "category": "backup",
            "version": str(manifest(slug).get("version") or ""),
            # Its code is fetched as root by opanelctl; the panel only loads
            # it and keeps its state, like a panel addon (see _is_panel_addon).
            "kind": "external",
            "packages": [],
            "service": "",
            "features": [],
            "notes": list(entry.get("notes", [])),
            "cli_only": True,
            "external": True,
        }
    return entries


def _trusted(path: Path) -> bool:
    """Root's, and writable by nobody else - the folder chain and every file."""
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return False
    return info.st_uid == 0 and not info.st_mode & 0o022


def check_trusted(slug: str) -> Path:
    """The addon's folder, once every part of it is seen to be root's."""
    folder = ROOT / slug
    if not folder.is_dir():
        raise AddonError(f"{slug} is not on this server; install it as root with: opanel addon install {slug}")
    for path in [ROOT, folder, *sorted(folder.rglob("*"))]:
        if "__pycache__" in path.parts:
            continue
        if not _trusted(path):
            raise AddonError(f"{path} is not root's alone; the {slug} addon is not loaded. "
                             f"Install it again as root: opanel addon install {slug}")
    return folder


def verify(slug: str) -> dict:
    """The addon's manifest, once its files are trusted and it fits this panel."""
    if slug not in KNOWN:
        raise AddonError(f"Unknown addon: {slug}")
    folder = check_trusted(slug)
    info = manifest(slug)
    if info.get("id") != slug:
        raise AddonError(f"{folder}/addon.json is not the {slug} addon's")
    if info.get("panel_api") != PANEL_API:
        raise AddonError(f"The {slug} addon is written for panel API {info.get('panel_api')}, this panel has "
                         f"{PANEL_API}; update the panel or the addon")
    module = str(info.get("module") or "")
    if not module.isidentifier() or not (folder / module / "__init__.py").is_file():
        raise AddonError(f"The {slug} addon names no module to load")
    return info


class PanelApi:
    """All an external addon gets from the panel. Kept small on purpose: the
    addon works on what it is handed and gives back what it changed."""

    panel = "opanel"
    api_version = PANEL_API

    def __init__(self, slug: str):
        from app.services.destinations import DestinationError

        self.slug = slug
        self.logger = logging.getLogger(f"opanel.addon.{slug}")
        # What a driver raises with a sentence the page shows: the addon does
        # not import the panel's modules, so it works on OPanel as well.
        self.DestinationError = DestinationError

    def register_destination(self, driver) -> None:
        from app.services import destinations

        destinations.register(driver, addon=self.slug)


def load(slug: str) -> dict:
    """Import one installed addon and let it register. Raises AddonError."""
    if slug in _loaded:
        return _loaded[slug]
    info = verify(slug)
    folder = ROOT / slug
    name = f"opanel_addon_{slug.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(
        name, folder / info["module"] / "__init__.py", submodule_search_locations=[str(folder / info["module"])])
    if spec is None or spec.loader is None:
        raise AddonError(f"The {slug} addon could not be read")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Nothing it writes may land beside it: the folder is root's, and a
    # __pycache__ there would be the panel user's.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if not callable(register):
            raise AddonError(f"The {slug} addon has no register()")
        register(PanelApi(slug))
    except AddonError:
        sys.modules.pop(name, None)
        raise
    except Exception as exc:  # noqa: BLE001 - whatever an addon does wrong stays its own
        sys.modules.pop(name, None)
        raise AddonError(f"The {slug} addon failed to load: {type(exc).__name__}: {exc}") from exc
    finally:
        sys.dont_write_bytecode = previous
    _loaded[slug] = info
    return info


def load_installed(is_installed: Callable[[str], bool]) -> list[str]:
    """Every known addon recorded as installed, loaded; one that fails is
    logged and left out, never stops the panel."""
    loaded = []
    for slug in KNOWN:
        if not is_installed(slug):
            continue
        try:
            load(slug)
            loaded.append(slug)
        except AddonError as exc:
            logger.warning("%s", exc)
    return loaded


def loaded(slug: str) -> Optional[dict]:
    return _loaded.get(slug)
