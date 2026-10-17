"""OpenLiteSpeed's per-client throttle, for the whole server (as SNPanel's, 2026-10-17).

Per visitor's address: requests per second (static files and PHP apart) and
connections - over the soft limit for longer than the grace period, the
address is refused for the ban period. Off until an administrator saves it
on: behind a proxy or a CDN every visitor of that edge would share one limit.
The loopback and the server's own addresses are always trusted.

What the administrator chose is kept with the panel's settings, so turning it
off and on again brings the same numbers back; "off" writes LiteSpeed's own
no-limit values.
"""
from __future__ import annotations

import json

from app.services import panel_settings
from app.services.shell import shell

FIELDS = ("static_rps", "dynamic_rps", "conn_soft", "conn_hard", "grace_secs", "ban_secs")
LIMITS = {
    "static_rps": (0, 100000), "dynamic_rps": (0, 10000), "conn_soft": (1, 100000),
    "conn_hard": (1, 100000), "grace_secs": (1, 3600), "ban_secs": (0, 86400),
}
# Generous for a person browsing - a page and its files over HTTP/2 is one
# connection and a few dozen requests - short for a flood.
DEFAULTS = {"static_rps": 200, "dynamic_rps": 20, "conn_soft": 60, "conn_hard": 120, "grace_secs": 15, "ban_secs": 60}
# LiteSpeed's values when nothing limits a client.
OFF = {"static_rps": 0, "dynamic_rps": 0, "conn_soft": 10000, "conn_hard": 10000, "grace_secs": 15, "ban_secs": 300}
CONFIG_KEYS = ("staticReqPerSec", "dynReqPerSec", "softLimit", "hardLimit", "gracePeriod", "banPeriod")
SETTING = "ols_throttle"


def saved() -> dict:
    stored = panel_settings._read_raw().get(SETTING) or {}
    values = {key: int(stored.get(key, DEFAULTS[key])) for key in FIELDS}
    return {"enabled": bool(stored.get("enabled", False)), **values}


def in_force() -> dict | None:
    """The values in LiteSpeed's configuration now, or None when it cannot be read."""
    result = shell.privileged("ols-throttle-get", check=False, fallback=["echo", "{}"])
    try:
        raw = json.loads((result.stdout or "").strip() or "{}")
    except ValueError:
        return None
    if not raw:
        return None
    values = {field: raw.get(key) for field, key in zip(FIELDS, CONFIG_KEYS)}
    values["limiting"] = any(values[key] not in (None, OFF[key]) for key in ("static_rps", "dynamic_rps", "conn_soft", "conn_hard"))
    return values


def validate(values: dict) -> dict:
    clean = {}
    for key in FIELDS:
        low, high = LIMITS[key]
        try:
            number = int(values[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a whole number") from exc
        if not low <= number <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        clean[key] = number
    if clean["conn_hard"] < clean["conn_soft"]:
        raise ValueError("The hard connection limit cannot be below the soft one")
    return clean


def apply(enabled: bool, values: dict) -> dict:
    clean = validate(values)
    written = clean if enabled else OFF
    shell.privileged("ols-throttle-set", helper_args=[str(written[key]) for key in FIELDS], fallback=["true"])
    data = panel_settings._read_raw()
    data[SETTING] = {"enabled": bool(enabled), **clean}
    panel_settings._write_raw(data)
    return saved()
