import json
import re
from typing import Iterable

from app.models.entities import Website
from app.services.shell import CommandResult, shell


DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
MAX_CUSTOM_BYTES = 64 * 1024
MAX_SITE_RULE_BYTES = 160 * 1024

DEFAULT_RULES = [
    {
        "id": "php-sensitive-files",
        "category": "PHP",
        "title": "PHP sensitive files",
        "description": "Blocks direct probes for PHP app secrets, Composer metadata, git data, and phpinfo files.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/\\.env(?:\\.|$)|/\\.user\\.ini(?:\\.|$)|/\\.git/|/composer\\.(?:json|lock)(?:$|[?])|/(?:phpinfo|info)\\.php(?:$|[?])|/(?:config|database|db)\\.php\\.(?:bak|old|save|txt)(?:$|[?]))" "id:1001301,phase:1,deny,status:403,log,msg:'opanel blocked PHP sensitive file probe'""",
    },
    {
        "id": "php-path-traversal",
        "category": "PHP",
        "title": "Path traversal",
        "description": "Blocks ../ and encoded traversal probes in URLs and query/form arguments.",
        "rules": """SecRule REQUEST_URI|ARGS "@rx (?i)(?:\\.\\./|\\.\\.\\\\|%2e%2e%2f|%252e%252e%252f)" "id:1001302,phase:2,deny,status:403,log,msg:'opanel blocked PHP path traversal'""",
    },
    {
        "id": "php-runtime-probes",
        "category": "PHP",
        "title": "PHP runtime probes",
        "description": "Blocks direct probes for common PHP webshell names and old PHPUnit RCE paths.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/(?:c99|r57|shell|cmd|wso)\\.php(?:$|[?])|/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin\\.php(?:$|[?]))" "id:1001303,phase:1,deny,status:403,log,msg:'opanel blocked PHP runtime probe'""",
    },
    {
        "id": "laravel-sensitive-files",
        "category": "Laravel",
        "title": "Laravel sensitive files",
        "description": "Blocks probes for Laravel environment files, logs, artisan, and cached PHP config.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/\\.env(?:\\.|$)|/artisan(?:$|[?])|/server\\.php(?:$|[?])|/storage/logs/[^?]*\\.log(?:$|[?])|/bootstrap/cache/[^?]*\\.php(?:$|[?]))" "id:1001201,phase:1,deny,status:403,log,msg:'opanel blocked Laravel sensitive path'""",
    },
    {
        "id": "laravel-ignition-rce",
        "category": "Laravel",
        "title": "Laravel Ignition RCE probes",
        "description": "Blocks direct probes for the old Laravel Ignition execute-solution endpoint.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/_ignition/execute-solution(?:$|[?]))" "id:1001202,phase:1,deny,status:403,log,msg:'opanel blocked Laravel Ignition RCE probe'""",
    },
    {
        "id": "wordpress-sensitive-files",
        "category": "WordPress",
        "title": "WordPress sensitive files",
        "description": "Blocks wp-config probes, uploads PHP execution probes, and internal WordPress PHP paths.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/wp-config\\.php(?:\\.|$|[?])|/wp-content/(?:uploads|cache|upgrade)/[^?]*\\.php(?:$|[?])|/wp-admin/includes/[^?]*\\.php(?:$|[?])|/wp-includes/[^?]*\\.php(?:$|[?]))" "id:1001101,phase:1,deny,status:403,log,msg:'opanel blocked WordPress sensitive path'""",
    },
    {
        "id": "wordpress-xmlrpc-author-scan",
        "category": "WordPress",
        "title": "WordPress author scans",
        "description": "Blocks ?author= enumeration scans while leaving XML-RPC compatibility to site policy.",
        "rules": """SecRule ARGS:author "@rx ^[0-9]+$" "id:1001103,phase:2,deny,status:403,log,msg:'opanel blocked WordPress author enumeration'""",
    },
    {
        "id": "wordpress-install-upgrade",
        "category": "WordPress",
        "title": "WordPress installer probes",
        "description": "Blocks direct access to WordPress installation scripts after deployment.",
        "rules": """SecRule REQUEST_URI "@rx (?i)(?:/wp-admin/install\\.php(?:$|[?])|/wp-admin/setup-config\\.php(?:$|[?]))" "id:1001104,phase:1,deny,status:403,log,msg:'opanel blocked WordPress installer probe'""",
    },
]

LEGACY_RULE_ID_MAP = {
    "general-sensitive-files": "php-sensitive-files",
    "general-path-traversal": "php-path-traversal",
    "general-command-injection": "php-runtime-probes",
    "general-sqli": None,
    "general-xss": None,
}


def _rule_ids() -> set[str]:
    return {rule["id"] for rule in DEFAULT_RULES}


def _validate_domain(domain: str) -> str:
    value = (domain or "").strip().lower()
    if not DOMAIN_RE.fullmatch(value):
        raise ValueError("Invalid domain")
    return value


def _validate_custom_rules(content: str) -> str:
    value = content or ""
    if "\x00" in value:
        raise ValueError("WAF rules cannot contain NUL bytes")
    if len(value.encode("utf-8")) > MAX_CUSTOM_BYTES:
        raise ValueError("WAF custom rules must be 64 KB or smaller")
    return value.replace("\r\n", "\n").strip()


def _parse_enabled_rule_ids(value: str | None) -> set[str]:
    valid = _rule_ids()
    if not value:
        return set(valid)
    try:
        raw = json.loads(value)
    except (TypeError, ValueError):
        return set(valid)
    if not isinstance(raw, list):
        return set(valid)
    selected = {
        LEGACY_RULE_ID_MAP.get(rule_id, rule_id)
        for item in raw
        for rule_id in [str(item)]
        if LEGACY_RULE_ID_MAP.get(rule_id, rule_id) in valid
    }
    return selected


def validate_enabled_rule_ids(rule_ids: Iterable[str]) -> list[str]:
    valid = _rule_ids()
    selected = []
    for rule_id in rule_ids:
        value = LEGACY_RULE_ID_MAP.get(str(rule_id), str(rule_id))
        if value is None:
            continue
        if value not in valid:
            raise ValueError(f"Unknown WAF rule: {value}")
        if value not in selected:
            selected.append(value)
    return selected


def default_rule_definitions() -> list[dict]:
    return [
        {
            "id": rule["id"],
            "category": rule["category"],
            "title": rule["title"],
            "description": rule["description"],
            "enabled_default": True,
        }
        for rule in DEFAULT_RULES
    ]


def site_rules_file(domain: str) -> str:
    safe_domain = _validate_domain(domain)
    return f"/usr/local/lsws/conf/opanel/waf/sites/{safe_domain}.conf"


def render_site_rules(domain: str, enabled_rule_ids: Iterable[str], custom_rules: str = "") -> str:
    safe_domain = _validate_domain(domain)
    enabled = set(validate_enabled_rule_ids(enabled_rule_ids))
    custom = _validate_custom_rules(custom_rules)
    chunks = [
        f"# OPanel WAF rules for {safe_domain}",
        "Include /usr/local/lsws/conf/opanel/waf/opanel-base.conf",
        "",
        "# OPanel selected default rules",
    ]
    for rule in DEFAULT_RULES:
        if rule["id"] not in enabled:
            continue
        chunks.append(f"# {rule['category']} - {rule['title']} ({rule['id']})")
        chunks.append(rule["rules"].strip())
        if rule.get("exceptions"):
            chunks.append(rule["exceptions"].strip())
    chunks.extend(["", "# OPanel custom rules"])
    if custom:
        chunks.append(custom)
    content = "\n".join(chunks).strip() + "\n"
    if len(content.encode("utf-8")) > MAX_SITE_RULE_BYTES:
        raise ValueError("WAF site rules are too large")
    return content


def website_enabled_rule_ids(website: Website) -> set[str]:
    return _parse_enabled_rule_ids(getattr(website, "waf_default_rules", ""))


def website_custom_rules(website: Website) -> str:
    return _validate_custom_rules(getattr(website, "waf_custom_rules", "") or "")


def sync_site_rules(domain: str, enabled_rule_ids: Iterable[str], custom_rules: str = "") -> CommandResult:
    safe_domain = _validate_domain(domain)
    content = render_site_rules(safe_domain, enabled_rule_ids, custom_rules)
    return shell.privileged(
        "waf-site-save",
        helper_args=[safe_domain],
        check=False,
        input=content,
        fallback=["bash", "-lc", "cat >/tmp/opanel-waf-site.conf && echo WAF site rules saved"],
    )


def sync_website_rules(website: Website) -> CommandResult:
    return sync_site_rules(website.domain, website_enabled_rule_ids(website), website_custom_rules(website))


def site_config(website: Website) -> dict:
    from app.services import openlitespeed as webserver

    enabled = website_enabled_rule_ids(website)
    return {
        "website_id": website.id,
        "domain": website.domain,
        "waf_enabled": bool(website.waf_enabled),
        "http_flood_enabled": bool(getattr(website, "http_flood_enabled", False)),
        "http_flood_config": webserver.http_flood_zone_name(website.domain),
        "rules_file": site_rules_file(website.domain),
        "default_rules": [
            {
                **rule,
                "enabled": rule["id"] in enabled,
                "enabled_default": True,
            }
            for rule in default_rule_definitions()
        ],
        "enabled_rule_ids": [rule["id"] for rule in DEFAULT_RULES if rule["id"] in enabled],
        "custom_rules": website_custom_rules(website),
    }


def save_website_config(website: Website, enabled_rule_ids: Iterable[str], custom_rules: str) -> CommandResult:
    selected = validate_enabled_rule_ids(enabled_rule_ids)
    custom = _validate_custom_rules(custom_rules)
    website.waf_default_rules = json.dumps(selected, ensure_ascii=True)
    website.waf_custom_rules = custom
    return sync_site_rules(website.domain, selected, custom)


def status():
    return shell.privileged(
        "waf-status",
        check=False,
        fallback=["bash", "-lc", "test -f /usr/local/lsws/conf/opanel/waf/opanel-base.conf && echo installed || echo not-installed"],
    )


def install_engine():
    return shell.privileged(
        "waf-install",
        check=False,
        fallback=["bash", "-lc", "apt-get update && apt-get install -y ols-modsecurity"],
    )


def update_rules():
    return shell.privileged(
        "waf-update",
        check=False,
        fallback=["bash", "-lc", "echo no WAF updater found"],
    )


def default_rules():
    return shell.privileged(
        "waf-default-rules",
        check=False,
        fallback=["bash", "-lc", "cat /usr/local/lsws/conf/opanel/waf/opanel-default.conf 2>/dev/null || true"],
    )


def custom_rules():
    return shell.privileged(
        "waf-custom-rules",
        check=False,
        fallback=["bash", "-lc", "cat /usr/local/lsws/conf/opanel/waf/opanel-custom.conf 2>/dev/null || true"],
    )


def save_custom_rules(content: str):
    return shell.privileged(
        "waf-custom-save",
        check=False,
        input=_validate_custom_rules(content),
        fallback=["bash", "-lc", "cat >/tmp/opanel-waf-custom.conf && echo WAF custom rules saved"],
    )
