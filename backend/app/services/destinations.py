"""Backup destinations an addon brings (Google Drive, 2026-11-16), beside the
panel's own SFTP and S3.

A driver is an object with:

    kind: str                 "gdrive" - stored in the destination's `kind`
    label: str                "Google Drive"
    fields: list[dict]        what the form asks for: {"name", "label", "type"
                              ("text" | "password" | "number"), "required",
                              "secret", "hint", "default"}
    auth: str | None          "oauth_device": signed in with a code shown on
                              the page (connect_start / connect_poll)

    validate(fields, config) -> dict      the config to keep, from the form
    summary(config) -> str                one line for the list
    connected(config) -> bool
    connect_start(config) -> dict         {"verification_url", "user_code",
                                           "expires_in", "interval"}
    connect_poll(config) -> str           "pending" | "done" | "denied" |
                                          "expired" | "slow_down"
    test(config) -> str                   writes and removes a probe; a message
    upload(config, local_file, remote_name, on_progress=None) -> str
    listing(config) -> list[dict]         {"key", "name", "size", "modified"};
                                          a key ends in "/<the file's name>"
                                          (Drive: "<file id>/<name>"), which
                                          is how Restore recognises the archive
    download(config, key, destination, on_progress=None) -> None
    delete(config, key) -> None

`config` is a plain dict the driver may change in place (a token refreshed, a
folder made); the panel stores it encrypted, whole, in the destination's
`config` column, and saves it again when a call changed it. A driver raises
DestinationError with a sentence the page can show.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator, Optional

from app.core.secrets import decrypt, encrypt

BUILTIN = ("sftp", "s3")


class DestinationError(RuntimeError):
    """A destination that cannot do what was asked, said for the page."""


_drivers: dict[str, object] = {}
_addon_of: dict[str, str] = {}


def register(driver, addon: str) -> None:
    kind = str(getattr(driver, "kind", "") or "")
    if not kind or kind in BUILTIN or not kind.isalnum() or len(kind) > 8:
        raise ValueError(f"An addon may not register the destination kind {kind!r}")
    _drivers[kind] = driver
    _addon_of[kind] = addon


def is_external(kind: Optional[str]) -> bool:
    return bool(kind) and kind not in BUILTIN


def ensure_loaded() -> None:
    """The installed external addons, loaded in this process too: the backup
    timer and the shell are processes of their own, not the API."""
    from app.services import addons, external_addons

    external_addons.load_installed(addons.is_enabled)


def driver_for(kind: str):
    """The driver of an addon kind, while its addon is installed and loaded."""
    from app.services import addons

    if kind not in _drivers:
        ensure_loaded()
    driver = _drivers.get(kind)
    addon = _addon_of.get(kind)
    if driver is None or not addon or not addons.is_enabled(addon):
        raise DestinationError(f"Destinations of the kind {kind!r} need their addon, which is not installed")
    return driver


def kinds() -> list[dict]:
    """What the destination form offers: the panel's own two, then each addon
    kind that is installed."""
    from app.services import addons

    ensure_loaded()
    out = [{"kind": "sftp", "label": "SFTP", "builtin": True, "fields": [], "auth": None},
           {"kind": "s3", "label": "S3 compatible", "builtin": True, "fields": [], "auth": None}]
    for kind, driver in sorted(_drivers.items()):
        if addons.is_enabled(_addon_of.get(kind, "")):
            out.append({"kind": kind, "label": getattr(driver, "label", kind), "builtin": False,
                        "fields": list(getattr(driver, "fields", []) or []),
                        "auth": getattr(driver, "auth", None)})
    return out


def label(kind: Optional[str]) -> str:
    if kind == "s3":
        return "S3"
    if not is_external(kind):
        return "SFTP"
    driver = _drivers.get(kind)
    return getattr(driver, "label", kind) if driver else str(kind)


def read_config(target) -> dict:
    raw = getattr(target, "config", None)
    if not raw:
        return {}
    try:
        value = json.loads(decrypt(raw))
    except (RuntimeError, ValueError) as exc:
        raise DestinationError("This destination's settings could not be read; edit it and save again") from exc
    return value if isinstance(value, dict) else {}


def write_config(target, config: dict) -> None:
    target.config = encrypt(json.dumps(config, ensure_ascii=True, sort_keys=True))


@contextmanager
def opened(target, db=None) -> Iterator[tuple[object, dict]]:
    """The driver and config of an addon destination; whatever the driver
    changed in the config (a refreshed token, a folder it made) is saved."""
    driver = driver_for(target.kind)
    config = read_config(target)
    before = json.dumps(config, sort_keys=True)
    try:
        yield driver, config
    finally:
        if json.dumps(config, sort_keys=True) != before:
            write_config(target, config)
            if db is not None:
                try:
                    db.commit()
                except Exception:  # noqa: BLE001 - the work done stands; the next call refreshes again
                    db.rollback()


def public_view(target) -> dict:
    """What the list shows of an addon destination: its kind's label, one
    line, and whether it is signed in. No secret leaves here."""
    if not is_external(target.kind):
        return {"kind_label": label(target.kind), "summary": "", "connected": True, "options": {}}
    try:
        driver = _drivers.get(target.kind)
        config = read_config(target)
        # The form's own fields, to edit it with - never one marked secret.
        shown = {field["name"]: config.get(field["name"])
                 for field in (getattr(driver, "fields", []) or []) if not field.get("secret")}
        return {"kind_label": label(target.kind),
                "summary": driver.summary(config) if driver else "addon not installed",
                "connected": bool(driver and driver.connected(config)), "options": shown}
    except DestinationError as exc:
        return {"kind_label": label(target.kind), "summary": str(exc), "connected": False, "options": {}}
