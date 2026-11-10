"""The panel's part of `opanel regenerate-secrets` (security, 2026-11-10).

A VPS cloned from an image starts with the image's secrets: the SECRET_KEY
that signs sessions and encrypts stored passwords, phpMyAdmin's sign-on and
cookie secrets, and the API and MCP tokens created on the image. Every clone
then accepts every other clone's sessions and tokens. This gives the server
keys of its own:

  - a new SECRET_KEY, and every stored value encrypted under the old one
    ("fernet:..." in any column of the database or any JSON file under
    /var/lib/opanel) encrypted again under the new one;
  - new PMA_SIGNON_SECRET and PMA_BLOWFISH_SECRET;
  - the API and MCP tokens deleted: they are stored as plain SHA-256, so the
    image's tokens worked on every clone. Whoever needs one creates it again.

Run by /usr/local/sbin/opanel-regenerate-secrets as the opanel user from
backend/, with the panel stopped. Nothing is written until every value has
been re-encrypted in memory; the .env goes last, so a failure before it leaves
the old key and the old values together. Prints one line of counts.
"""
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path

from app.core.config import settings
from app.core.secrets import decrypt, encrypt_with

PREFIX = "fernet:"
ENV_FILE = Path(".env")
DATA_DIR = Path(os.environ.get("opanel_DATA_DIR", "/var/lib/opanel"))
TOKEN_TABLES = ("api_tokens", "mcp_tokens")


class Rekey:
    def __init__(self, new_key: str):
        self.new_key = new_key
        self.moved = 0
        self.unreadable = 0

    def value(self, stored):
        if not isinstance(stored, str) or not stored.startswith(PREFIX):
            return stored
        try:
            plain = decrypt(stored)
        except RuntimeError:
            # Already unreadable under the current key: nothing to carry over.
            self.unreadable += 1
            return stored
        self.moved += 1
        return encrypt_with(self.new_key, plain)

    def tree(self, node):
        if isinstance(node, dict):
            return {key: self.tree(value) for key, value in node.items()}
        if isinstance(node, list):
            return [self.tree(value) for value in node]
        return self.value(node)


def _database_path() -> Path:
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"only a SQLite database is supported, not {url}")
    return Path(url[len("sqlite:///"):])


def _database_changes(conn: sqlite3.Connection, rekey: Rekey) -> list[tuple[str, str, int, str]]:
    changes = []
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
        for column in columns:
            rows = conn.execute(
                f'SELECT rowid, "{column}" FROM "{table}" WHERE typeof("{column}") = \'text\' AND "{column}" LIKE \'fernet:%\'')
            for rowid, stored in rows.fetchall():
                updated = rekey.value(stored)
                if updated != stored:
                    changes.append((table, column, rowid, updated))
    return changes


def _json_changes(rekey: Rekey) -> list[tuple[Path, str]]:
    changes = []
    if not DATA_DIR.is_dir():
        return changes
    for path in sorted(DATA_DIR.rglob("*.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if PREFIX not in text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        updated = rekey.tree(data)
        if updated != data:
            if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
                raise SystemExit(f"{path} holds encrypted values but is not writable by this user")
            changes.append((path, json.dumps(updated, ensure_ascii=False, indent=2) + "\n"))
    return changes


def _env_text(values: dict[str, str]) -> str:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    seen = set()
    for index, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if name in values:
            lines[index] = f"{name}={values[name]}"
            seen.add(name)
    lines += [f"{name}={value}" for name, value in values.items() if name not in seen]
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".regen-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


def main() -> int:
    new_values = {
        "SECRET_KEY": secrets.token_hex(32),
        "PMA_SIGNON_SECRET": secrets.token_hex(32),
        "PMA_BLOWFISH_SECRET": secrets.token_hex(32),
    }
    rekey = Rekey(new_values["SECRET_KEY"])
    env_text = _env_text(new_values)
    json_changes = _json_changes(rekey)

    conn = sqlite3.connect(_database_path())
    try:
        db_changes = _database_changes(conn, rekey)
        tokens = 0
        with conn:
            for table, column, rowid, updated in db_changes:
                conn.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?', (updated, rowid))
            for table in TOKEN_TABLES:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
                    tokens += conn.execute(f'DELETE FROM "{table}"').rowcount
    finally:
        conn.close()
    for path, text in json_changes:
        _write(path, text)
    _write(ENV_FILE, env_text)
    print(f"re-encrypted {rekey.moved} stored value(s), {rekey.unreadable} unreadable left as they were; "
          f"deleted {tokens} API/MCP token(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
