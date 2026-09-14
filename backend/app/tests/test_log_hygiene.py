"""Logs must not be able to fill the disk.

Measured on two live servers before any of this was written: 97 GB under
/usr/local/lsws/logs and 43 GB under /var/log/openlitespeed on one box, against
3.7 MB of actual cache. Three separate causes, each guarded below.
"""
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
INSTALL = (PROJECT_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
UPDATE = (PROJECT_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")


def _rotation_block() -> str:
    start = HELPER.index("ensure_site_log_rotation() {")
    return HELPER[start:HELPER.index("\n# OpenLiteSpeed ships logLevel DEBUG", start)]


# --------------------------------------------------------------------------
# Rotation policy: daily, seven days, uncompressed
# --------------------------------------------------------------------------

def test_rotation_is_daily_and_keeps_seven_days():
    block = _rotation_block()

    assert "daily" in block
    assert "rotate 7" in block
    assert "weekly" not in block
    assert "rotate 8" not in block


def test_logs_are_left_uncompressed():
    """Kept readable on purpose: grep beats zcat when a site is misbehaving."""
    block = _rotation_block()

    assert "nocompress" in block
    # delaycompress left the largest rotation uncompressed anyway, which is how
    # a single php_error.log.1 reached 36 GB.
    assert "delaycompress" not in block
    assert "\n    compress" not in block


def test_every_log_openlitespeed_writes_is_covered():
    block = _rotation_block()

    for pattern in (
        "/var/log/openlitespeed/*/php_error.log",   # per-site PHP errors
        "/var/log/openlitespeed/*.access.log",      # per-site access, 219 files on one box
        "/usr/local/lsws/logs/error.log",
        "/usr/local/lsws/logs/stderr.log",          # the one that reached 60 GB
    ):
        assert pattern in block, pattern


def test_a_runaway_log_rotates_before_the_daily_run():
    # One site wrote 5 GB a day. Without maxsize it would wait for midnight.
    assert re.search(r"maxsize\s+\d+[KMG]", _rotation_block())


def test_copytruncate_is_used_because_openlitespeed_holds_the_handle():
    assert "copytruncate" in _rotation_block()


# --------------------------------------------------------------------------
# OpenLiteSpeed's own date-stamped archives, which it never deletes
# --------------------------------------------------------------------------

def test_old_openlitespeed_archives_are_removed():
    block = _rotation_block()
    start = block.index("postrotate")
    script = block[start:block.index("endscript", start)]

    assert "-mtime +7" in script
    assert "-delete" in script
    # Anchored to the date-stamped archive form, so a live log never matches.
    assert "[0-9]{4}_[0-9]{2}_[0-9]{2}" in script
    assert "-maxdepth 1" in script


@pytest.mark.parametrize("name,should_match", [
    ("stderr.log.2026_08_26", True),
    ("stderr.log.2026_08_26.04", True),
    ("error.log.2026_09_03.01", True),
    ("error.log", False),
    ("stderr.log", False),
    ("access.log", False),
    ("error.log.1", False),
])
def test_the_archive_pattern_matches_archives_only(name, should_match):
    """Verified against the real directory on a live box: 39 archives matched,
    96.8 GB, and none of the files being written to."""
    pattern = re.compile(r".*\.log\.[0-9]{4}_[0-9]{2}_[0-9]{2}(\.[0-9]+)?$")

    assert bool(pattern.match(name)) is should_match, name


# --------------------------------------------------------------------------
# The two settings that produced the volume in the first place
# --------------------------------------------------------------------------

def test_jit_is_disabled_so_ioncube_stops_warning_on_every_worker_start():
    """PHP prints 'JIT is incompatible with third party extensions' once per
    worker because the ionCube loader installs a user opcode handler. That
    warning alone wrote a 60 GB stderr.log."""
    start = HELPER.index("upload_max_filesize = 1024M")
    block = HELPER[start:HELPER.index("INI", start)]

    assert "opcache.jit = disable" in block
    assert "opcache.jit_buffer_size = 0" in block


def test_server_log_level_is_brought_down_from_openlitespeed_default():
    start = HELPER.index("ensure_ols_server_log_level() {")
    block = HELPER[start:HELPER.index("\n}", start)]

    assert "logLevel" in block and "WARN" in block
    assert "DEBUG" in block            # only rewrites when it finds DEBUG
    assert "httpd_config.conf" in block
    assert ".bak" in block             # keeps a copy before editing


def test_journal_is_capped():
    start = HELPER.index("ensure_journal_cap() {")
    block = HELPER[start:HELPER.index("\n}", start)]

    assert "SystemMaxUse" in block
    # Never a second time: an admin who raised it keeps their value.
    assert "grep -qE '^[[:space:]]*SystemMaxUse=' \"$conf\" && return 0" in block


# --------------------------------------------------------------------------
# Reaching existing boxes, not just new ones
# --------------------------------------------------------------------------

def test_the_updater_applies_this_to_existing_installs():
    """The boxes with 97 GB of logs are the ones already running."""
    assert "opanel-helper log-hygiene" in UPDATE
    assert "opanel-helper log-hygiene" in INSTALL


def test_rotation_config_is_versioned_so_it_can_be_replaced():
    """The first version returned early whenever the file existed, so no
    existing box would ever have received a fix."""
    block = _rotation_block()

    assert "LOG_ROTATION_VERSION" in HELPER
    assert "opanel log rotation v" in block
    assert "[[ -f /etc/logrotate.d/opanel-sites ]] && return 0" not in HELPER


def test_helper_exposes_it_as_one_subcommand():
    assert "  log-hygiene)" in HELPER
    start = HELPER.index("  log-hygiene)")
    block = HELPER[start:HELPER.index(";;", start)]

    for call in ("ensure_site_log_rotation", "ensure_ols_server_log_level", "ensure_journal_cap"):
        assert call in block, call


# --------------------------------------------------------------------------
# JIT on an existing box, where nothing rewrites 99-opanel.ini
# --------------------------------------------------------------------------

def test_php_tuning_no_longer_asks_for_jit():
    """recommend_php_config is what kept putting opcache.jit back: it wrote
    1255 into 99-opanel.ini every time the panel applied PHP tuning."""
    from app.services import php

    config = php.recommend_php_config()

    assert config["opcache_jit"] == "disable"
    assert config["opcache_jit_buffer_size"] == 0


def test_rendered_tuning_ini_disables_jit():
    from app.services import php

    source = (PROJECT_ROOT / "backend" / "app" / "services" / "php.py").read_text(encoding="utf-8")

    assert "opcache.jit              = {cfg['opcache_jit']}" in source
    # No M suffix: the value is 0, and "0M" would only be confusing.
    assert "{cfg['opcache_jit_buffer_size']}M" not in source


def test_existing_installs_get_jit_turned_off():
    start = HELPER.index("ensure_php_jit_disabled() {")
    block = HELPER[start:HELPER.index("\n}", start)]

    # Keeps the key name. Writing the replacement without the backreference
    # would have left a bare " disable" line and broken every php.ini.
    assert r"s/^([[:space:]]*opcache\.jit[[:space:]]*=).*/\1 disable/" in block
    assert r"s/^([[:space:]]*opcache\.jit_buffer_size[[:space:]]*=).*/\1 0/" in block
    # Adds the keys when the file predates them.
    assert r"printf '\nopcache.jit = disable\nopcache.jit_buffer_size = 0\n'" in block
    # Does nothing on a second run.
    assert "disable' \"$ini\" && continue" in block
    assert "ensure_php_jit_disabled" in HELPER[HELPER.index("  log-hygiene)"):][:400]
