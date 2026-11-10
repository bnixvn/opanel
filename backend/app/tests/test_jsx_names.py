"""Every capitalised JSX tag is imported or declared in its file (2026-11-10).

ESLint's core no-undef does not look at JSX names: an unimported PasswordField
blanked OPanel's Add user page (a ReferenceError at render), and the build and
the lint both passed. This reads each .jsx file for the names its tags use and
the names it imports, declares or destructures."""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
FILES = sorted(path for path in SRC.rglob("*.jsx") if "node_modules" not in path.parts)


def _known(code: str) -> set[str]:
    known = {"React"}
    for match in re.finditer(r"import\s+(.+?)\s+from\s+['\"]", code, re.S):
        known.update(re.findall(r"[A-Za-z_]\w*", match.group(1)))
    known.update(re.findall(r"(?:function|class|const|let|var)\s+([A-Z]\w*)", code))
    for group in re.findall(r"(?:const|let|var)\s*\{([^}]*)\}\s*=", code):
        known.update(re.findall(r"[A-Za-z_]\w*", group))
    for group in re.findall(r"\(\s*\{([^}]*)\}", code):           # ({ icon: Icon, ... }) =>
        known.update(re.findall(r"[A-Za-z_]\w*", group))
    for group in re.findall(r"\(\s*\[([^\]]*)\]\s*\)", code):     # ([mode, label, Icon]) =>
        known.update(re.findall(r"[A-Za-z_]\w*", group))
    known.update(re.findall(r"([A-Z]\w*)\s*:", code))
    return known


@pytest.mark.parametrize("path", FILES, ids=lambda path: str(path.relative_to(SRC)))
def test_every_jsx_component_is_imported_or_declared(path):
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    used = set(re.findall(r"<([A-Z]\w*)[\s/>.]", code))
    missing = sorted(used - _known(code))
    assert not missing, f"{path.name} uses {missing} without importing them"
