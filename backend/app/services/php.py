from pathlib import Path

from app.core.config import settings
from app.schemas.schemas import PhpConfigUpdate
from app.services.shell import shell

SUPPORTED_PHP_VERSIONS = ("5.6", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5")


def _safe_ini_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Invalid PHP ini value")
    return value


def update_php_ini(payload: PhpConfigUpdate) -> str:
    php_version = payload.php_version
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")
    display_errors = "On" if str(payload.display_errors).lower() in {"1", "true", "on", "yes"} else "Off"
    content = "\n".join([
        f"display_errors = {display_errors}",
        f"memory_limit = {_safe_ini_value(payload.memory_limit)}",
        f"upload_max_filesize = {_safe_ini_value(payload.upload_max_filesize)}",
        f"post_max_size = {_safe_ini_value(payload.post_max_size)}",
        f"max_execution_time = {int(payload.max_execution_time)}",
        f"max_input_time = {int(payload.max_input_time)}",
        f"max_input_vars = {int(payload.max_input_vars)}",
        "",
    ])
    # LSPHP config path: /usr/local/lsws/lsphp{ver}/etc/php.d/99-opanel.ini
    lsphp_ver = php_version.replace(".", "")
    target = Path(f"/usr/local/lsws/lsphp{lsphp_ver}/etc/php.d/99-opanel.ini")
    if settings.command_dry_run:
        return content
    shell.privileged(
        "php-config-write",
        helper_args=[php_version],
        input=content,
        fallback=[
            "bash",
            "-lc",
            "cat > /usr/local/lsws/lsphp$2/etc/php.d/99-opanel.ini && /usr/local/lsws/bin/lswsctrl restart",
            "opanel-php-config-write",
            php_version,
            lsphp_ver,
        ],
    )
    return str(target)


PHP_CONFIG_KEYS = {
    "display_errors": "Off",
    "memory_limit": "512M",
    "upload_max_filesize": "1024M",
    "post_max_size": "1024M",
    "max_execution_time": "300",
    "max_input_time": "600",
    "max_input_vars": "10000",
}


def default_php_config(php_version: str) -> dict:
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")
    return {
        "php_version": php_version,
        "display_errors": PHP_CONFIG_KEYS["display_errors"],
        "memory_limit": PHP_CONFIG_KEYS["memory_limit"],
        "upload_max_filesize": PHP_CONFIG_KEYS["upload_max_filesize"],
        "post_max_size": PHP_CONFIG_KEYS["post_max_size"],
        "max_execution_time": int(PHP_CONFIG_KEYS["max_execution_time"]),
        "max_input_time": int(PHP_CONFIG_KEYS["max_input_time"]),
        "max_input_vars": int(PHP_CONFIG_KEYS["max_input_vars"]),
    }


def restore_default_php_ini(php_version: str) -> str:
    return update_php_ini(PhpConfigUpdate(**default_php_config(php_version)))


def read_php_ini(php_version: str) -> dict:
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")
    values = dict(PHP_CONFIG_KEYS)
    lsphp_ver = php_version.replace(".", "")
    for path in [
        Path(f"/usr/local/lsws/lsphp{lsphp_ver}/etc/php.ini"),
        Path(f"/usr/local/lsws/lsphp{lsphp_ver}/etc/php.d/99-opanel.ini"),
    ]:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith(";") or "=" not in line:
                continue
            key, value = [part.strip() for part in line.split("=", 1)]
            if key in values:
                values[key] = value
    values["php_version"] = php_version
    values["max_execution_time"] = int(values["max_execution_time"])
    values["max_input_time"] = int(values["max_input_time"])
    values["max_input_vars"] = int(values["max_input_vars"])
    return values


def list_installed_php() -> list[str]:
    """List PHP versions that are currently installed (LSPHP)."""
    installed = []
    for version in SUPPORTED_PHP_VERSIONS:
        lsphp_ver = version.replace(".", "")
        lsphp_bin = Path(f"/usr/local/lsws/lsphp{lsphp_ver}/bin/lsphp")
        if lsphp_bin.exists():
            installed.append(version)
    return sorted(installed, key=lambda v: [int(x) for x in v.split(".")])


def install_php(php_version: str) -> dict:
    """Install a PHP version or repair the opanel extension set via apt."""
    if php_version not in SUPPORTED_PHP_VERSIONS:
        allowed = ", ".join(sorted(SUPPORTED_PHP_VERSIONS))
        raise ValueError(f"Unsupported PHP version. Allowed: {allowed}")

    lsphp_ver = php_version.replace(".", "")
    already_installed = Path(f"/usr/local/lsws/lsphp{lsphp_ver}/bin/lsphp").exists()

    if settings.command_dry_run:
        action = "repair" if already_installed else "install"
        return {"status": "dry_run", "message": f"Would {action} lsphp{lsphp_ver} and opanel extensions"}

    result = shell.privileged(
        "php-install",
        helper_args=[php_version],
        fallback=[
            "apt-get",
            "install",
            "-y",
            f"lsphp{lsphp_ver}",
            f"lsphp{lsphp_ver}-common",
            f"lsphp{lsphp_ver}-mysql",
            f"lsphp{lsphp_ver}-sqlite3",
            f"lsphp{lsphp_ver}-curl",
            f"lsphp{lsphp_ver}-gd",
            f"lsphp{lsphp_ver}-mbstring",
            f"lsphp{lsphp_ver}-xml",
            f"lsphp{lsphp_ver}-zip",
            f"lsphp{lsphp_ver}-opcache",
            f"lsphp{lsphp_ver}-intl",
            f"lsphp{lsphp_ver}-bcmath",
            f"lsphp{lsphp_ver}-redis",
            f"lsphp{lsphp_ver}-imagick",
        ],
    )
    return {"status": "ensured" if already_installed else "installed", "version": php_version, "output": result.stdout}
