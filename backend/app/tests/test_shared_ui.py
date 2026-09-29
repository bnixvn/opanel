"""The shared BNIX interface (frontend/src/shared) is the same in OPanel and
BPanel. It is edited in OPanel only and copied across by
frontend/scripts/sync-shared-ui.mjs, which also writes MANIFEST; a file changed
in one repo alone no longer matches, and this fails."""
import hashlib
from pathlib import Path

SHARED = Path(__file__).resolve().parents[3] / "frontend" / "src" / "shared"
TEXT = {".css", ".md", ".txt", ".js", ".jsx", ".json"}


def _digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def test_the_shared_interface_matches_its_manifest():
    listed = {}
    for line in (SHARED / "MANIFEST").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        listed[rel] = digest
    present = {p.relative_to(SHARED).as_posix() for p in SHARED.rglob("*") if p.is_file() and p.name != "MANIFEST"}
    assert present == set(listed), "files added or removed outside sync-shared-ui.mjs"
    changed = [rel for rel, digest in listed.items() if _digest(SHARED / rel) != digest]
    assert not changed, f"changed in this repo alone -- edit in OPanel and sync: {changed}"
