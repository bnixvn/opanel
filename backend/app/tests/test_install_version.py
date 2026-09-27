"""A fresh install knows which release it is.

The panel reads its version from /opt/opanel/VERSION. install.sh copied only
backend/ and frontend/, so a brand-new box showed the fallback version
(1.0.44) and "update available" for the very release it had just installed,
until its first update copied the file. update.sh always did.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
UPDATE = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    return source[start: source.index("\n}\n", start)]


def test_install_copies_the_version_file():
    body = _function(INSTALL, "copy_sources")
    assert 'install -m 0644 "${PROJECT_ROOT}/VERSION" "${APP_DIR}/VERSION"' in body


def test_update_does_the_same():
    assert 'install -m 0644 "$SOURCE_DIR/VERSION" "$APP_DIR/VERSION"' in UPDATE


def test_the_panel_reads_that_file():
    version_py = (ROOT / "backend" / "app" / "core" / "version.py").read_text(encoding="utf-8")
    assert 'parents[3] / "VERSION"' in version_py
