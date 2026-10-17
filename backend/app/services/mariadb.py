from datetime import datetime
import contextlib
import tempfile
from pathlib import Path
import os
import re
import secrets
import string
import subprocess
from typing import Dict

from app.core.config import settings
from app.services.shell import shell


IDENTIFIER_CHARS = set(string.ascii_lowercase + string.digits + "_")


def random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#%^*_+-"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def assert_db_user_available(db, db_user: str, owner_id: int) -> None:
    """Refuse a db_user that a different panel account already holds.

    ``allow_existing=True`` turns creation into ``ALTER USER ... IDENTIFIED BY``,
    which is only safe under the precondition its docstring states -- that the
    caller already owns the account. Nothing enforced that. Both archive paths
    took ``db_user`` from archive content and checked only the sibling field
    ``db_name``, so an archive could name another tenant's SQL user and
    re-password it: the victim's site lost database access, and the archive's
    author held a working credential with GRANT ALL on the victim's database,
    reachable from localhost, which is where every tenant's PHP runs.

    RESERVED_DB_IDENTIFIERS already protects the server's own accounts; this
    protects ordinary tenants from each other. Import it lazily to keep this
    module free of a model-layer import at load time.
    """
    from app.models.entities import DatabaseAccount

    if not db_user:
        return
    clash = (
        db.query(DatabaseAccount)
        .filter(
            DatabaseAccount.db_user == db_user,
            DatabaseAccount.owner_id != owner_id,
        )
        .first()
    )
    if clash is not None:
        raise ValueError(f"Database user already belongs to another account: {db_user}")
    # The panel's table is not the whole picture. create_account (the WHMCS
    # path) calls create_database directly and never inserts a DatabaseAccount
    # row, so those SQL users -- whose names are derived deterministically from
    # the domain -- are invisible here. identifier_in_use asks MariaDB itself,
    # which is why the user-facing create path uses it. Without this, an archive
    # could still re-password an account the panel has no record of.
    if db.query(DatabaseAccount).filter(DatabaseAccount.db_user == db_user).first() is None:
        existing = identifier_in_use("opanel_probe_unused", db_user)
        if existing and "user" in existing.lower():
            raise ValueError(
                f"Database user already exists on this server: {db_user}"
            )


def safe_db_identifier(domain: str, prefix: str) -> str:
    clean = "".join(ch if ch.isalnum() else "_" for ch in domain.lower())[:38]
    return f"{prefix}_{clean}"[:63]


# Names the panel must never create, drop or re-password. The panel's own SQL
# account holds GRANT ALL ON *.* WITH GRANT OPTION, so without this an ordinary
# panel user could ask for db_user="root" and take the server's root account.
RESERVED_DB_IDENTIFIERS = frozenset({
    "root",
    "mysql",
    "opanel",
    "bpanel",
    "sys",
    "information_schema",
    "performance_schema",
    "debian-sys-maint",
    "mariadb",
})


def _validate_identifier(value: str) -> str:
    if not value or len(value) > 64 or any(ch not in IDENTIFIER_CHARS for ch in value):
        raise ValueError("Invalid database identifier")
    if value.lower() in RESERVED_DB_IDENTIFIERS:
        raise ValueError(f"'{value}' is reserved and cannot be used")
    return value


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return f"`{_validate_identifier(value)}`"


def _mysql_args(extra: list = None) -> list:
    args = ["mysql"]
    default_files = [
        Path.home() / ".my.cnf",
        Path("/opt/opanel/.my.cnf"),
    ]
    defaults_file = next((path for path in default_files if path.exists()), None)
    if defaults_file:
        args.append(f"--defaults-file={defaults_file}")
    if extra:
        args.extend(extra)
    return args


def _run_sql(sql: str, *, check: bool = True):
    """Pipe SQL through stdin so secrets never appear in argv/ps output."""
    return shell.run(_mysql_args(), check=check, input=sql, sensitive=True)


def create_database(seed: str, prefix: str = "wp", db_name: str | None = None, if_not_exists: bool = True) -> Dict[str, str]:
    db_name = _validate_identifier(db_name or safe_db_identifier(seed, prefix))
    db_user = _validate_identifier(safe_db_identifier(db_name, "u"))
    db_password = random_password()
    create_clause = "CREATE DATABASE IF NOT EXISTS" if if_not_exists else "CREATE DATABASE"
    sql = (
        f"{create_clause} {_quote_identifier(db_name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        f"ALTER USER {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        f"GRANT ALL PRIVILEGES ON {_quote_identifier(db_name)}.* TO {_quote_sql_string(db_user)}@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )
    _run_sql(sql)
    return {"db_name": db_name, "db_user": db_user, "db_password": db_password}


def create_database_credentials(
    db_name: str,
    db_user: str,
    db_password: str,
    *,
    allow_existing: bool = False,
) -> Dict[str, str]:
    """Create a database and its user.

    By default a name already in use is an error. `allow_existing=True` restores
    the older re-password-on-collision behaviour and belongs only to the restore
    and import flows, which recreate accounts they already own; on the
    user-facing create path it let anyone re-password an existing SQL account.
    """
    db_name = _validate_identifier(db_name)
    db_user = _validate_identifier(db_user)
    if allow_existing:
        sql = (
            f"CREATE DATABASE IF NOT EXISTS {_quote_identifier(db_name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"CREATE USER IF NOT EXISTS {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
            f"ALTER USER {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        )
    else:
        sql = (
            f"CREATE DATABASE {_quote_identifier(db_name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"CREATE USER {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        )
    sql += (
        f"GRANT ALL PRIVILEGES ON {_quote_identifier(db_name)}.* TO {_quote_sql_string(db_user)}@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )
    _run_sql(sql)
    return {"db_name": db_name, "db_user": db_user, "db_password": db_password}


def identifier_in_use(db_name: str, db_user: str) -> str:
    """Say whether the name or user already exists in MariaDB itself.

    The panel's own table is not enough: a database created outside the panel,
    or a system account, is invisible there.
    """
    db_name = _validate_identifier(db_name)
    db_user = _validate_identifier(db_user)
    sql = (
        "SELECT 'db' FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = "
        f"{_quote_sql_string(db_name)} UNION ALL SELECT 'user' FROM mysql.user "
        f"WHERE user = {_quote_sql_string(db_user)} AND host = 'localhost';"
    )
    result = shell.run(
        [*_mysql_args(), "-N", "-B"], check=False, input=sql, sensitive=True
    )
    if getattr(result, "returncode", 1) != 0:
        return ""
    found = {line.strip() for line in (result.stdout or "").splitlines()}
    if "db" in found:
        return "Database name already exists on this server"
    if "user" in found:
        return "Database user already exists on this server"
    return ""


def database_sizes(db_names) -> Dict[str, Dict[str, int]]:
    """Bytes (data and indexes) and table count of each named database.

    One query for all of them. A name MariaDB has no tables for comes back as
    0 bytes and 0 tables; a name that is not a valid identifier is left out.
    An empty answer means MariaDB could not be asked.
    """
    names = sorted({str(name) for name in db_names if name and all(ch in IDENTIFIER_CHARS for ch in str(name))})
    if not names:
        return {}
    sql = (
        "SELECT TABLE_SCHEMA, COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0), COUNT(*) "
        "FROM information_schema.TABLES WHERE TABLE_SCHEMA IN ("
        + ", ".join(_quote_sql_string(name) for name in names)
        + ") GROUP BY TABLE_SCHEMA;"
    )
    result = shell.run([*_mysql_args(), "-N", "-B"], check=False, input=sql, sensitive=True)
    if getattr(result, "returncode", 1) != 0:
        return {}
    sizes = {name: {"size_bytes": 0, "tables": 0} for name in names}
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] not in sizes:
            continue
        try:
            sizes[parts[0]] = {"size_bytes": int(float(parts[1])), "tables": int(parts[2])}
        except ValueError:
            continue
    return sizes



# --- Databases MariaDB has and the panel does not --------------------------------
# Made with the mysql client, left by an import or a removed website's owner:
# an administrator lists them and adopts each into an account.

# Schemas nobody adopts: MariaDB's own, and phpMyAdmin's configuration storage.
SYSTEM_SCHEMAS = frozenset({"information_schema", "mysql", "performance_schema", "sys", "phpmyadmin"})


class MariaDBError(RuntimeError):
    """MariaDB could not be asked."""


def _rows(sql: str) -> list[list[str]]:
    # --raw: a grant on `shop\_db` comes back as written, not with doubled backslashes.
    result = shell.run([*_mysql_args(), "-N", "-B", "--raw"], check=False, input=sql, sensitive=True)
    if getattr(result, "returncode", 1) != 0:
        raise MariaDBError((result.stderr or result.stdout or "mysql failed").strip()[:300])
    return [line.split("\t") for line in (result.stdout or "").splitlines() if line]


def valid_identifier(value: str) -> bool:
    try:
        _validate_identifier(value)
    except ValueError:
        return False
    return True


def grant_matches(db_name: str, pattern: str) -> bool:
    r"""Whether a database-level grant on `pattern` reaches `db_name`.

    The grant names a LIKE pattern: `_` and `%` are wildcards, `\_` and `\%`
    the characters themselves.
    """
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append("." if ch == "_" else ".*" if ch == "%" else re.escape(ch))
        i += 1
    return re.fullmatch("".join(out), db_name, re.S) is not None


def grant_is_literal(db_name: str, pattern: str) -> bool:
    """A grant on this name, as the panel writes it or with `_` escaped."""
    return pattern in (db_name, db_name.replace("_", "\\_"))


def schemas() -> list[dict]:
    """Every database with its bytes and table count, the system's own left out."""
    rows = _rows(
        "SELECT s.SCHEMA_NAME, COALESCE(SUM(t.DATA_LENGTH + t.INDEX_LENGTH), 0), COUNT(t.TABLE_NAME) "
        "FROM information_schema.SCHEMATA s LEFT JOIN information_schema.TABLES t "
        "ON t.TABLE_SCHEMA = s.SCHEMA_NAME GROUP BY s.SCHEMA_NAME ORDER BY s.SCHEMA_NAME;"
    )
    out = []
    for row in rows:
        if len(row) != 3 or row[0] in SYSTEM_SCHEMAS:
            continue
        try:
            out.append({"name": row[0], "size_bytes": int(float(row[1])), "tables": int(row[2])})
        except ValueError:
            continue
    return out


def db_grants() -> list[dict]:
    """The database-level grants: who reaches which name pattern."""
    return [{"user": row[0], "host": row[1], "db": row[2]}
            for row in _rows("SELECT User, Host, Db FROM mysql.db;") if len(row) == 3]


def schema_exists(db_name: str) -> bool:
    return bool(_rows(
        f"SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = {_quote_sql_string(db_name)};"
    ))


def account_hosts(db_user: str) -> list[str]:
    return sorted(row[0] for row in _rows(
        f"SELECT Host FROM mysql.user WHERE User = {_quote_sql_string(db_user)};"
    ) if row)


_GRANT_LINE = re.compile(r"^GRANT (?P<privs>.+?) ON (?P<object>.+?) TO ", re.S)


def reaches_beyond(db_user: str, db_name: str) -> list[str]:
    """What else `db_user`@localhost may do besides this one database.

    Adopting an account hands the new owner all of it, so anything listed
    here means a new account has to be made instead.
    """
    rows = _rows(f"SHOW GRANTS FOR {_quote_sql_string(_validate_identifier(db_user))}@'localhost';")
    beyond = []
    for row in rows:
        line = row[0]
        match = _GRANT_LINE.match(line)
        if not match:
            beyond.append(line.split(" TO ")[0][:80])
            continue
        privs, target = match.group("privs").strip(), match.group("object").strip()
        if target == "*.*":
            if privs.upper() != "USAGE":
                beyond.append("*.*")
            continue
        named = re.match(r"`((?:[^`]|``)+)`\.", target)
        pattern = named.group(1).replace("``", "`") if named else target
        if not named or not grant_is_literal(db_name, pattern):
            beyond.append(pattern)
    return beyond


def adopt_account(db_name: str, db_user: str, db_password: str, *, existing: bool) -> None:
    """Give a database an account the panel knows the password of.

    An `existing` account gets a new password and a grant on the database; a
    new one is made. The database itself is not touched.
    """
    db_name, db_user = _validate_identifier(db_name), _validate_identifier(db_user)
    who = f"{_quote_sql_string(db_user)}@'localhost'"
    verb = "ALTER USER" if existing else "CREATE USER"
    _run_sql(
        f"{verb} {who} IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        f"GRANT ALL PRIVILEGES ON {_quote_identifier(db_name)}.* TO {who};\n"
        "FLUSH PRIVILEGES;\n"
    )


def revoke_db_grant(user: str, host: str, pattern: str) -> None:
    """Take a database-level grant away from an account, as MariaDB names it."""
    target = "`" + pattern.replace("`", "``") + "`.*"
    who = f"{_quote_sql_string(user)}@{_quote_sql_string(host)}"
    _run_sql(f"REVOKE ALL PRIVILEGES ON {target} FROM {who};\n")
    # A grant made WITH GRANT OPTION keeps that part apart from ALL PRIVILEGES.
    _run_sql(f"REVOKE GRANT OPTION ON {target} FROM {who};\n", check=False)
    _run_sql("FLUSH PRIVILEGES;\n")


def drop_database(db_name: str, db_user: str):
    sql = (
        f"DROP DATABASE IF EXISTS {_quote_identifier(db_name)};\n"
        f"DROP USER IF EXISTS {_quote_sql_string(_validate_identifier(db_user))}@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )
    return _run_sql(sql)


def change_database_password(db_user: str, db_password: str):
    sql = (
        f"ALTER USER {_quote_sql_string(_validate_identifier(db_user))}@'localhost' "
        f"IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        "FLUSH PRIVILEGES;\n"
    )
    return _run_sql(sql)


def export_database(db_name: str, output_file: str):
    args = ["mysqldump"]
    default_files = [
        Path.home() / ".my.cnf",
        Path("/opt/opanel/.my.cnf"),
    ]
    defaults_file = next((path for path in default_files if path.exists()), None)
    if defaults_file:
        args.append(f"--defaults-file={defaults_file}")
    args.extend([_validate_identifier(db_name), "--result-file", output_file])
    return shell.run(args, sensitive=True)


@contextlib.contextmanager
def _scoped_defaults_file(db_user: str, db_password: str):
    """A 0600 [client] defaults-file for one database user.

    The credentials must not go in argv: /proc/<pid>/cmdline is world-readable,
    so every local uid -- including every other tenant's PHP -- could read the
    password of the schema being imported.
    """
    safe_user = _validate_identifier(db_user)
    if "\n" in db_password or "\r" in db_password or "\x00" in db_password:
        raise ValueError("Invalid database password")
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="opanel-mysql-", suffix=".cnf", delete=False
    )
    try:
        os.chmod(handle.name, 0o600)
        handle.write("[client]\n")
        handle.write(f"user={safe_user}\n")
        handle.write(f"password={db_password}\n")
        handle.write("host=localhost\n")
        handle.close()
        yield handle.name
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def import_database(
    db_name: str,
    input_file: str,
    *,
    as_user: str | None = None,
    as_password: str | None = None,
):
    """Load a SQL dump into ``db_name``.

    ``as_user``/``as_password`` name the schema's own MariaDB account, and every
    caller that imports a dump out of an archive must supply them. Without them
    this runs as the panel's own account, which holds
    GRANT ALL PRIVILEGES ON *.* WITH GRANT OPTION -- and `mysql <db>` only sets
    the default schema, so a dump body could GRANT itself anything, create
    users, or read another tenant's tables. Scoping the connection makes
    MariaDB enforce the one-schema boundary instead of trusting the file.
    """
    safe_name = _validate_identifier(db_name)
    sql_path = Path(input_file).resolve()
    if not sql_path.exists() or not sql_path.is_file():
        raise FileNotFoundError("SQL file not found")
    if settings.command_dry_run:
        return shell.run(["mysql", safe_name], sensitive=True)

    def _run(args):
        with sql_path.open("rb") as source:
            return subprocess.run(args, stdin=source, capture_output=True, check=False)

    if as_user:
        if not as_password:
            raise ValueError("A scoped database import needs the account password")
        with _scoped_defaults_file(as_user, as_password) as defaults:
            completed = _run(["mysql", f"--defaults-file={defaults}", safe_name])
    else:
        completed = _run(_mysql_args([safe_name]))
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"Database import failed: {stderr.strip()}")
    return completed


def dump_database_file(db_name: str, output_dir: Path) -> Path:
    safe_name = _validate_identifier(db_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{safe_name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.sql"
    export_database(safe_name, str(target))
    if settings.command_dry_run and not target.exists():
        target.write_text(f"-- DRY RUN database dump for {safe_name}\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# MariaDB auto-tuner
# ---------------------------------------------------------------------------
MARIADB_CONF_DIR = Path("/etc/mysql/mariadb.conf.d")
# The one tuning file. The helper's mariadb-retune writes it on install, on
# every update and from the panel; it is the only MariaDB tuner (operator,
# 2026-10-06). This module used to carry a second one, with 65% of the RAM for
# the buffer pool and 400 connections, written to a 99- file that MariaDB read
# after this one -- so on a hosting server it took the memory the PHP workers
# needed. The helper deletes that file when it finds it.
TUNING_CONF_FILE = MARIADB_CONF_DIR / "90-opanel-tuning.cnf"


def _detect_ram_mb() -> int:
    """Return total system RAM in MiB from /proc/meminfo."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 1024  # safe fallback


def _detect_cpu_cores() -> int:
    """Return number of logical CPU cores."""
    try:
        import os
        return os.cpu_count() or 1
    except Exception:
        return 1


def _detect_is_ssd() -> bool:
    """Heuristic: check rotational flag for root disk."""
    try:
        import glob
        for dev in glob.glob("/sys/block/*/queue/rotational"):
            val = Path(dev).read_text().strip()
            if val == "0":
                return True
    except Exception:
        pass
    return False  # assume HDD if unknown


def _preview() -> dict:
    result = shell.privileged("mariadb-tune-preview", check=False, fallback=["true"])
    values: dict = {}
    for line in (result.stdout or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            values[key.strip()] = value.strip()
    return values


def recommend_mariadb_config() -> dict:
    """What mariadb-retune would write now, as the helper works it out."""
    values = _preview()
    cfg = {
        "ram_mb": int(values.get("ram_mb") or _detect_ram_mb()),
        "cores": int(values.get("cores") or _detect_cpu_cores()),
        "is_ssd": _detect_is_ssd(),
    }
    for key in ("innodb_buffer_pool_size", "innodb_log_file_size", "tmp_table_size", "max_allowed_packet"):
        cfg[key] = values.get(key, "")
    cfg["max_heap_table_size"] = cfg["tmp_table_size"]
    for key in ("max_connections", "thread_cache_size", "table_open_cache", "innodb_io_capacity",
                "innodb_data_mb", "php_workers"):
        value = values.get(key, "")
        cfg[key] = int(value) if value.isdigit() else None
    return cfg


def apply_mariadb_tuning() -> dict:
    """Retune through the helper; MariaDB restarts only if a setting changed.
    The buffer pool is an input to the PHP worker plan, so that follows."""
    result = shell.privileged("mariadb-retune", check=False, fallback=["true"])
    cfg = recommend_mariadb_config()
    cfg["restart_returncode"] = result.returncode
    cfg["message"] = (result.stdout or result.stderr or "").strip()
    cfg["conf_path"] = str(TUNING_CONF_FILE)
    try:
        import threading

        from app.services import php_workers

        threading.Thread(target=php_workers.reconcile_quietly, name="opanel-php-workers", daemon=True).start()
    except Exception:  # noqa: BLE001 - the retune itself has happened
        pass
    return cfg


def read_mariadb_tuning() -> dict | None:
    """The tuning file as it is now."""
    if not TUNING_CONF_FILE.exists():
        return None
    return {
        "conf_path": str(TUNING_CONF_FILE),
        "content": TUNING_CONF_FILE.read_text(encoding="utf-8"),
    }
