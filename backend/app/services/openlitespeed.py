"""
OpenLiteSpeed (OLS) webserver service layer for OPanel.

Manages vhost configs under /usr/local/lsws/conf/opanel/,
included from the main httpd_config.conf.
Replaces the former nginx.py service.
"""

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from app.core.config import settings
from app.services import site_users
from app.services.shell import shell

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
OLS_CONF_ROOT = Path("/usr/local/lsws/conf")
OPANEL_CONF_DIR = OLS_CONF_ROOT / "opanel"
OPANEL_VHOSTS_DIR = OPANEL_CONF_DIR / "vhosts"
OPANEL_MODSEC_DIR = OPANEL_CONF_DIR / "modsec"
OPANEL_CUSTOM_DIR = OPANEL_CONF_DIR / "custom"
OPANEL_SSL_DIR = OPANEL_CONF_DIR / "ssl" / "sites"
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "openlitespeed"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALLOWED_PHP_VERSIONS = {"5.6", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5"}
ALLOWED_APP_TYPES = {"wordpress", "php", "static"}
ALLOWED_REWRITE_MODES = {"none", "front_controller", "laravel", "codeigniter", "seohburl"}
ALLOWED_LOG_KINDS = {"access", "error"}
DOMAIN_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
MAX_FULL_CONFIG_BYTES = 128 * 1024

HTTP_FLOOD_DEFAULTS = {
    "access_limit_requests": 100,
    "access_limit_window": 10,
    "access_limit_burst": 100,
    "connection_limit": 60,
}

WORDPRESS_CSP = (
    "default-src 'self' https: data: blob:; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; "
    "style-src 'self' 'unsafe-inline' https:; "
    "img-src 'self' data: https: blob:; "
    "font-src 'self' data: https:; "
    "connect-src 'self' https:; "
    "frame-src 'self' https: blob:; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self' https:; "
    "frame-ancestors 'self'; "
    "upgrade-insecure-requests"
)

# ---------------------------------------------------------------------------
# Jinja2 renderer
# ---------------------------------------------------------------------------
_jinja_env: Optional[Environment] = None


def _get_jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _jinja_env


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_domain(domain: str) -> str:
    safe_domain = (domain or "").strip().lower()
    if not DOMAIN_RE.fullmatch(safe_domain):
        raise ValueError("Invalid domain")
    return safe_domain


def _safe_alias_domains(aliases: list[str] | tuple[str, ...] | None) -> list[str]:
    safe_aliases: list[str] = []
    seen: set[str] = set()
    for alias in aliases or []:
        safe_alias = _safe_domain(str(alias))
        if safe_alias in seen:
            continue
        safe_aliases.append(safe_alias)
        seen.add(safe_alias)
    return safe_aliases


def _check_php_version(php_version: str | None) -> str | None:
    if php_version is None:
        return None
    if php_version not in ALLOWED_PHP_VERSIONS:
        raise ValueError(f"Unsupported PHP version: {php_version}")
    return php_version


def _check_app_type(app_type: str) -> str:
    if app_type not in ALLOWED_APP_TYPES:
        raise ValueError(f"Unsupported app type: {app_type}")
    return app_type


def _check_rewrite_mode(mode: str | None) -> str:
    value = (mode or "none").strip().lower()
    if value not in ALLOWED_REWRITE_MODES:
        raise ValueError(f"Unsupported rewrite mode: {mode}")
    return value


def _check_log_kind(kind: str) -> str:
    value = (kind or "").strip().lower()
    if value not in ALLOWED_LOG_KINDS:
        raise ValueError("Log kind must be access or error")
    return value


def _effective_document_root(document_root: str, rewrite_mode: str) -> str:
    safe_root = site_users.validate_document_root(document_root)
    if rewrite_mode in {"laravel", "codeigniter"} and safe_root.rstrip("/") == "public_html":
        return "public_html/public"
    return safe_root


def _lsphp_binary(php_version: str) -> str:
    """Return the path to the LSPHP binary for a given PHP version."""
    ver = php_version.replace(".", "")
    return f"/usr/local/lsws/lsphp{ver}/bin/lsphp"


def _lsphp_listener_name(php_version: str) -> str:
    """Return the OLS external app / listener name for a PHP version."""
    ver = php_version.replace(".", "")
    return f"lsphp{ver}"


def _http_flood_value(value, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def validate_http_flood_config(raw=None) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "access_limit_requests": _http_flood_value(raw.get("access_limit_requests"), HTTP_FLOOD_DEFAULTS["access_limit_requests"], 1, 100000),
        "access_limit_window": _http_flood_value(raw.get("access_limit_window"), HTTP_FLOOD_DEFAULTS["access_limit_window"], 1, 3600),
        "access_limit_burst": _http_flood_value(raw.get("access_limit_burst"), HTTP_FLOOD_DEFAULTS["access_limit_burst"], 0, 100000),
        "connection_limit": _http_flood_value(raw.get("connection_limit"), HTTP_FLOOD_DEFAULTS["connection_limit"], 1, 10000),
    }


def http_flood_config_for_website(website) -> dict:
    return validate_http_flood_config(getattr(website, "http_flood_config", "") or "")


def http_flood_zone_name(domain: str) -> str:
    safe_domain = _safe_domain(domain)
    digest = hashlib.sha1(safe_domain.encode("utf-8")).hexdigest()[:12]
    return f"opanel_hf_{digest}"


def _http_flood_rate(config: dict) -> str:
    """Convert requests/window to an OLS-compatible rate string."""
    requests = max(1, int(config["access_limit_requests"]))
    window = max(1, int(config["access_limit_window"]))
    # OLS uses req/sec in throttling
    rate_per_sec = max(1, math.ceil(requests / window))
    return str(rate_per_sec)


# ---------------------------------------------------------------------------
# WAF paths
# ---------------------------------------------------------------------------
def waf_rules_file(domain: str) -> str:
    safe_domain = _safe_domain(domain)
    return str(OPANEL_MODSEC_DIR / "sites" / f"{safe_domain}.conf")


# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------
def _vhost_dir(domain: str) -> Path:
    safe_domain = _safe_domain(domain)
    return OPANEL_VHOSTS_DIR / safe_domain


def _vhost_conf_path(domain: str) -> Path:
    return _vhost_dir(domain) / "vhost.conf"


def _log_path(domain: str, kind: str) -> Path:
    safe_domain = _safe_domain(domain)
    safe_kind = _check_log_kind(kind)
    return Path("/var/log/openlitespeed") / f"{safe_domain}.{safe_kind}.log"


# ---------------------------------------------------------------------------
# Custom config validation
# ---------------------------------------------------------------------------
DANGEROUS_DIRECTIVES_RE = re.compile(
    r"(?mi)^\s*("
    r"include\b|"
    r"loadModule\b|"
    r"user\s|"
    r"daemon\s|"
    r"pid\s|"
    r"workingDir\b|"
    r"extprocessor\s|"
    r"context\s|"
    r"vhDomain\s|"
    r"docRoot\s|"
    r"errorlog\s|"
    r"accesslog\s|"
    r"realm\s|"
    r"authName\s|"
    r"allowOverride\s"
    r")"
)


def validate_custom_directives(content: Optional[str]) -> str:
    """Sanitize and validate custom OLS directives."""
    if not content:
        return ""
    text = content.replace("\r\n", "\n").strip()
    if len(text) > 16 * 1024:
        raise ValueError("Custom directives block is too large")
    if "\x00" in text:
        raise ValueError("Custom directives block contains a NUL byte")
    # Check for balanced braces
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth < 0:
            raise ValueError("Unbalanced braces in custom directives block")
    if depth != 0:
        raise ValueError("Unbalanced braces in custom directives block")
    # Reject dangerous directives
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if DANGEROUS_DIRECTIVES_RE.match(stripped):
            raise ValueError(f"Dangerous directive rejected: {stripped.split()[0] if stripped.split() else stripped}")
    return text


def validate_full_config(content: Optional[str]) -> str:
    """Validate a full OLS vhost config block."""
    if not content:
        raise ValueError("Config is required")
    text = content.replace("\r\n", "\n").strip()
    if len(text) > MAX_FULL_CONFIG_BYTES:
        raise ValueError("Config is too large")
    if "\x00" in text:
        raise ValueError("Config contains a NUL byte")
    # Must contain a vhost definition
    if "docRoot" not in text and "vhRoot" not in text:
        raise ValueError("Config must contain a vhost definition with docRoot or vhRoot")
    return text


# ---------------------------------------------------------------------------
# Rewrite rules per mode
# ---------------------------------------------------------------------------
REWRITE_RULES = {
    "none": "",
    "front_controller": (
        "rewrite {\n"
        "    enable              1\n"
        "    rewriteRules        <<<END_RULES\n"
        "RewriteCond %{REQUEST_FILENAME} !-f\n"
        "RewriteCond %{REQUEST_FILENAME} !-d\n"
        "RewriteRule ^(.*)$ index.php [QSA,L]\n"
        "END_RULES\n"
        "}\n"
    ),
    "laravel": (
        "rewrite {\n"
        "    enable              1\n"
        "    rewriteRules        <<<END_RULES\n"
        "RewriteCond %{REQUEST_FILENAME} !-f\n"
        "RewriteCond %{REQUEST_FILENAME} !-d\n"
        "RewriteRule ^(.*)$ index.php [QSA,L]\n"
        "END_RULES\n"
        "}\n"
    ),
    "codeigniter": (
        "rewrite {\n"
        "    enable              1\n"
        "    rewriteRules        <<<END_RULES\n"
        "RewriteCond %{REQUEST_FILENAME} !-f\n"
        "RewriteCond %{REQUEST_FILENAME} !-d\n"
        "RewriteCond $1 !^(index\\.php)\n"
        "RewriteRule ^(.*)$ index.php/$1 [QSA,L]\n"
        "END_RULES\n"
        "}\n"
    ),
    "seohburl": (
        "rewrite {\n"
        "    enable              1\n"
        "    rewriteRules        <<<END_RULES\n"
        "RewriteCond %{REQUEST_FILENAME} !-f\n"
        "RewriteCond %{REQUEST_FILENAME} !-d\n"
        "RewriteRule ^([^?]*) index.php?_url_=$1 [QSA,L]\n"
        "END_RULES\n"
        "}\n"
    ),
}


# ---------------------------------------------------------------------------
# Core rendering
# ---------------------------------------------------------------------------
def _build_context(
    domain: str,
    root_path: str,
    *,
    app_type: str = "wordpress",
    php_version: str | None = "8.3",
    document_root: str = "public_html",
    rewrite_mode: str = "none",
    custom_directives: str = "",
    ssl_enabled: bool = False,
    ssl_cert_path: str | None = None,
    ssl_key_path: str | None = None,
    ssl_ca_path: str | None = None,
    aliases: list[str] | None = None,
    redirects: list[dict] | None = None,
    waf_enabled: bool = False,
    http_flood_enabled: bool = False,
    http_flood_config: dict | None = None,
    linux_user: str | None = None,
    lsphp_socket_override: str | None = None,
) -> dict:
    """Build the template context for vhost rendering."""
    safe_domain = _safe_domain(domain)
    checked_app = _check_app_type(app_type)
    checked_php = _check_php_version(php_version) if checked_app != "static" else None
    checked_rewrite = _check_rewrite_mode(rewrite_mode)
    safe_doc_root = _effective_document_root(document_root, checked_rewrite)
    safe_aliases = _safe_alias_domains(aliases)

    # Determine LSPHP external app
    lsphp_app = ""
    lsphp_path = ""
    lsphp_socket = ""
    if checked_php and checked_app != "static":
        lsphp_app = _lsphp_listener_name(checked_php)
        lsphp_path = _lsphp_binary(checked_php)
        lsphp_socket = lsphp_socket_override or f"/tmp/lshttpd/{lsphp_app}.sock"

    rewrite_block = REWRITE_RULES.get(checked_rewrite, "")

    return {
        "domain": safe_domain,
        "root_path": root_path,
        "document_root": safe_doc_root,
        "app_type": checked_app,
        "php_version": checked_php,
        "lsphp_app": lsphp_app,
        "lsphp_path": lsphp_path,
        "lsphp_socket": lsphp_socket,
        "rewrite_mode": checked_rewrite,
        "rewrite_block": rewrite_block,
        "custom_directives": validate_custom_directives(custom_directives),
        "ssl_enabled": ssl_enabled,
        "ssl_cert_path": ssl_cert_path or "",
        "ssl_key_path": ssl_key_path or "",
        "ssl_ca_path": ssl_ca_path or "",
        "aliases": safe_aliases,
        "redirects": redirects or [],
        "waf_enabled": waf_enabled,
        "waf_rules_file": waf_rules_file(safe_domain) if waf_enabled else "",
        "http_flood_enabled": http_flood_enabled,
        "http_flood_config": http_flood_config or {},
        "http_flood_zone": http_flood_zone_name(safe_domain) if http_flood_enabled else "",
        "linux_user": linux_user or "www-data",
        "access_log": str(_log_path(safe_domain, "access")),
        "error_log": str(_log_path(safe_domain, "error")),
        "csp_header": WORDPRESS_CSP if checked_app == "wordpress" else "",
    }


def render_vhost(
    domain: str,
    root_path: str,
    *,
    app_type: str = "wordpress",
    php_version: str | None = "8.3",
    document_root: str = "public_html",
    rewrite_mode: str = "none",
    custom_directives: str = "",
    ssl_enabled: bool = False,
    ssl_cert_path: str | None = None,
    ssl_key_path: str | None = None,
    ssl_ca_path: str | None = None,
    aliases: list[str] | None = None,
    redirects: list[dict] | None = None,
    waf_enabled: bool = False,
    http_flood_enabled: bool = False,
    http_flood_config: dict | None = None,
    linux_user: str | None = None,
    lsphp_socket_override: str | None = None,
) -> str:
    """Render an OLS vhost config from the template."""
    ctx = _build_context(
        domain, root_path,
        app_type=app_type,
        php_version=php_version,
        document_root=document_root,
        rewrite_mode=rewrite_mode,
        custom_directives=custom_directives,
        ssl_enabled=ssl_enabled,
        ssl_cert_path=ssl_cert_path,
        ssl_key_path=ssl_key_path,
        ssl_ca_path=ssl_ca_path,
        aliases=aliases,
        redirects=redirects,
        waf_enabled=waf_enabled,
        http_flood_enabled=http_flood_enabled,
        http_flood_config=http_flood_config,
        linux_user=linux_user,
        lsphp_socket_override=lsphp_socket_override,
    )
    env = _get_jinja_env()
    template_name = f"{ctx['app_type']}.conf.j2"
    try:
        template = env.get_template(template_name)
    except Exception:
        template = env.get_template("php.conf.j2")
    return template.render(**ctx)


def rewrite_vhost(
    domain: str,
    root_path: str,
    **kwargs,
) -> str:
    """Render and write a vhost config to disk, then restart OLS."""
    content = render_vhost(domain, root_path, **kwargs)
    safe_domain = _safe_domain(domain)
    vhost_dir = _vhost_dir(safe_domain)
    vhost_dir.mkdir(parents=True, exist_ok=True)
    conf_path = _vhost_conf_path(safe_domain)
    conf_path.write_text(content, encoding="utf-8")
    # Validate and reload OLS
    test_result = shell.privileged("ols-vhost-reload", check=False, fallback=[
        "bash", "-lc",
        f"/usr/local/lsws/bin/lswsctrl restart 2>/dev/null || /usr/local/lsws/bin/openlitespeed restart 2>/dev/null || true",
    ])
    return content


def remove_vhost(domain: str) -> None:
    """Remove a vhost config directory."""
    safe_domain = _safe_domain(domain)
    vhost_dir = _vhost_dir(safe_domain)
    if vhost_dir.exists():
        import shutil
        shutil.rmtree(vhost_dir)


def get_vhost_config(domain: str) -> str | None:
    """Read the current vhost config for a domain."""
    conf_path = _vhost_conf_path(domain)
    if conf_path.exists():
        return conf_path.read_text(encoding="utf-8")
    return None


# ---------------------------------------------------------------------------
# HTTP Flood (OLS rewrite-based throttling)
# ---------------------------------------------------------------------------
def _http_flood_rewrites(domain: str, config: dict) -> str:
    """Generate OLS rewrite rules for HTTP flood protection."""
    zone = http_flood_zone_name(domain)
    rate = _http_flood_rate(config)
    burst = config.get("access_limit_burst", 0)
    connections = config.get("connection_limit", 60)
    return (
        f"# OPANEL HTTP FLOOD BEGIN\n"
        f"# Throttle zone: {zone}, rate: {rate}/s, burst: {burst}, maxConn: {connections}\n"
        f"# Managed by OPanel — do not edit manually\n"
        f"extprocessor {zone} {{\n"
        f"    type                    proxy\n"
        f"    address                 127.0.0.1:1\n"
        f"    maxConns                {connections}\n"
        f"    initTimeout             10\n"
        f"    retryTimeout            0\n"
        f"    respBuffer              0\n"
        f"}}\n"
        f"# OPANEL HTTP FLOOD END"
    )


# ---------------------------------------------------------------------------
# LSCache (replaces FastCGI cache)
# ---------------------------------------------------------------------------
LSCACHE_REWRITE = (
    "# OPANEL LSCACHE BEGIN\n"
    "rewrite {\n"
    "    enable              1\n"
    "    rewriteRules        <<<END_RULES\n"
    "RewriteCond %{REQUEST_METHOD} POST\n"
    "RewriteRule .* - [E=Cache-Control:v=no-cache]\n"
    "RewriteCond %{QUERY_STRING} !=\"\"\n"
    "RewriteRule .* - [E=Cache-Control:v=no-cache]\n"
    "RewriteCond %{HTTP_COOKIE} (comment_author|wordpress_[a-f0-9]+|wordpress_logged_in|wp-postpass) [NC]\n"
    "RewriteRule .* - [E=Cache-Control:v=no-cache]\n"
    "END_RULES\n"
    "}\n"
    "# OPANEL LSCACHE END"
)


# ---------------------------------------------------------------------------
# SSL helpers
# ---------------------------------------------------------------------------
def ssl_paths(domain: str) -> dict[str, str]:
    """Return managed SSL paths for a domain."""
    safe_domain = _safe_domain(domain)
    base = str(OPANEL_SSL_DIR / safe_domain)
    return {
        "cert": f"{base}/cert.crt",
        "key": f"{base}/privkey.key",
        "ca": f"{base}/ca.crt",
    }


# ---------------------------------------------------------------------------
# Config test
# ---------------------------------------------------------------------------
def test_config() -> bool:
    """Test OLS configuration validity."""
    result = shell.privileged("ols-config-test", check=False, fallback=[
        "bash", "-lc",
        "/usr/local/lsws/bin/lswsctrl restart 2>&1 | grep -qi 'error' && exit 1 || exit 0",
    ])
    return result.returncode == 0


def reload_service() -> None:
    """Reload OpenLiteSpeed."""
    shell.privileged("ols-reload", check=False, fallback=[
        "bash", "-lc",
        "/usr/local/lsws/bin/lswsctrl restart 2>/dev/null || /usr/local/lsws/bin/openlitespeed restart 2>/dev/null || true",
    ])


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------
def read_log(domain: str, kind: str = "access", lines: int = 200) -> str:
    """Read the last N lines of a site log."""
    safe_domain = _safe_domain(domain)
    safe_kind = _check_log_kind(kind)
    log_file = _log_path(safe_domain, safe_kind)
    if not log_file.exists():
        return ""
    try:
        count = int(lines)
    except (TypeError, ValueError):
        raise ValueError("Log lines must be a number")
    if count < 1 or count > 5000:
        raise ValueError("Log lines must be between 1 and 5000")
    try:
        result = shell.run(["tail", "-n", str(count), str(log_file)], check=False)
        return result.stdout
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------
def clear_cache(domain: str | None = None) -> None:
    """Clear LSCache for a domain or all domains."""
    if domain:
        safe_domain = _safe_domain(domain)
        cache_dir = Path(f"/tmp/lscache/{safe_domain}")
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir)
    else:
        # Clear all OPanel-managed cache
        cache_root = Path("/tmp/lscache")
        if cache_root.exists():
            import shutil
            shutil.rmtree(cache_root)
            cache_root.mkdir(parents=True, exist_ok=True)
