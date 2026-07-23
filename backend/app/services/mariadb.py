from datetime import datetime
from pathlib import Path
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


def safe_db_identifier(domain: str, prefix: str) -> str:
    clean = "".join(ch if ch.isalnum() else "_" for ch in domain.lower())[:38]
    return f"{prefix}_{clean}"[:63]


def _validate_identifier(value: str) -> str:
    if not value or len(value) > 64 or any(ch not in IDENTIFIER_CHARS for ch in value):
        raise ValueError("Invalid database identifier")
    return value


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return f"`{_validate_identifier(value)}`"


def _mysql_args(extra: list = None) -> list:
    args = ["mysql"]
    home_cnf = Path.home() / ".my.cnf"
    if home_cnf.exists():
        args.append(f"--defaults-file={home_cnf}")
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


def create_database_credentials(db_name: str, db_user: str, db_password: str) -> Dict[str, str]:
    db_name = _validate_identifier(db_name)
    db_user = _validate_identifier(db_user)
    sql = (
        f"CREATE DATABASE IF NOT EXISTS {_quote_identifier(db_name)} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        f"CREATE USER IF NOT EXISTS {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        f"ALTER USER {_quote_sql_string(db_user)}@'localhost' IDENTIFIED BY {_quote_sql_string(db_password)};\n"
        f"GRANT ALL PRIVILEGES ON {_quote_identifier(db_name)}.* TO {_quote_sql_string(db_user)}@'localhost';\n"
        "FLUSH PRIVILEGES;\n"
    )
    _run_sql(sql)
    return {"db_name": db_name, "db_user": db_user, "db_password": db_password}


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
    home_cnf = Path.home() / ".my.cnf"
    if home_cnf.exists():
        args.append(f"--defaults-file={home_cnf}")
    args.extend([_validate_identifier(db_name), "--result-file", output_file])
    return shell.run(args, sensitive=True)


def import_database(db_name: str, input_file: str):
    safe_name = _validate_identifier(db_name)
    sql_path = Path(input_file).resolve()
    if not sql_path.exists() or not sql_path.is_file():
        raise FileNotFoundError("SQL file not found")
    if settings.command_dry_run:
        return shell.run(["mysql", safe_name], sensitive=True)
    args = _mysql_args([safe_name])
    with sql_path.open("rb") as source:
        completed = subprocess.run(args, stdin=source, capture_output=True, check=False)
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
