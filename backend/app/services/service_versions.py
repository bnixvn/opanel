"""Versions of the services the panel runs, and updating them one at a time.

"Update OS now" upgrades every package on the server. This is the narrower
tool the operator asked for on 2026-10-21: see which version of OpenLiteSpeed,
each PHP, MariaDB and the rest is installed and which one apt offers, then
update one service or all of them. Nightly unattended-upgrades covers the same
packages (52opanel-service-updates); this is for not waiting until night.

The helper re-checks every package name against its own allow-list, so this
module decides what a service is, and the helper decides what may be touched.
"""
from __future__ import annotations

import re
from fnmatch import fnmatch

from app.services.shell import shell

UNIT_LOG_MARK = "opanel-service-update: finished (exit "

# key, label, the package whose version stands for the service, its packages.
# A service shows only when its main package is installed, so the Email, DNS
# and malware addons' daemons appear once those addons are.
SERVICES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("openlitespeed", "OpenLiteSpeed", "openlitespeed", ("openlitespeed",)),
    ("mariadb", "MariaDB", "mariadb-server", ("mariadb-*", "libmariadb*", "galera-*")),
    ("redis", "Redis", "redis-server", ("redis-server", "redis-tools")),
    ("nodejs", "Node.js", "nodejs", ("nodejs",)),
    ("exim", "Exim", "exim4-base", ("exim4*",)),
    ("dovecot", "Dovecot", "dovecot-core", ("dovecot-*",)),
    ("rspamd", "Rspamd", "rspamd", ("rspamd",)),
    ("powerdns", "PowerDNS", "pdns-server", ("pdns-*",)),
    ("clamav", "ClamAV", "clamav", ("clamav*", "libclamav*")),
)
LSPHP = re.compile(r"^lsphp(\d)(\d+)$")


def short_version(version: str) -> str:
    """8.4.26-1+noble -> 8.4.26, 1:10.11.14-0ubuntu0.24.04.1 -> 10.11.14."""
    version = re.sub(r"^\d+:", "", version or "")
    version = version.rsplit("-", 1)[0] if "-" in version else version
    return re.sub(r"\+dfsg\d*$", "", version)


def _shown(installed: str, offered: str) -> tuple[str, str]:
    """Short versions, unless an update keeps the upstream version.

    Ubuntu's security fixes usually do (7.0.15-1build2 -> 7.0.15-1ubuntu0.5),
    and "7.0.15 -> 7.0.15" would read as no update at all.
    """
    short_installed, short_offered = short_version(installed), short_version(offered)
    if offered and short_installed == short_offered:
        return re.sub(r"^\d+:", "", installed), re.sub(r"^\d+:", "", offered)
    return short_installed, short_offered if offered else ""


def _parse(stdout: str) -> dict:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in (stdout or "").splitlines():
        if line.startswith("== "):
            current = line[3:].strip()
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    installed: dict[str, str] = {}
    for line in sections.get("installed", []):
        parts = line.split()
        if len(parts) >= 2:
            installed[parts[0].split(":", 1)[0]] = parts[1]
    upgradable: dict[str, str] = {}
    for line in sections.get("upgradable", []):
        parts = line.split()
        if len(parts) >= 2:
            upgradable[parts[0].split(":", 1)[0]] = parts[1]
    state = next((line.strip() for line in sections.get("state", []) if line.strip()), "inactive")
    return {
        "installed": installed,
        "upgradable": upgradable,
        "running": state in {"active", "activating", "deactivating", "reloading"},
        "log": [line for line in sections.get("log", []) if line.strip()],
        "check_error": "\n".join(sections.get("warning", [])).strip(),
    }


def _services(installed: dict[str, str]) -> list[tuple[str, str, str, tuple[str, ...]]]:
    php = []
    for name in installed:
        match = LSPHP.match(name)
        if match:
            major, minor = match.groups()
            php.append((int(major), int(minor), name))
    found = [SERVICES[0]]
    for major, minor, name in sorted(php, reverse=True):
        found.append((f"php{major}{minor}", f"PHP {major}.{minor}", name, (name, f"{name}-*")))
    found.extend(SERVICES[1:])
    return [service for service in found if service[2] in installed]


def _last_result(log: list[str]) -> dict | None:
    for line in reversed(log):
        if UNIT_LOG_MARK in line:
            code = line.split(UNIT_LOG_MARK, 1)[1].split(")", 1)[0]
            return {"ok": code == "0", "exit_code": int(code) if code.isdigit() else None}
    return None


def status(refresh: bool = False) -> dict:
    result = shell.privileged(
        "service-packages",
        helper_args=["refresh"] if refresh else [],
        check=False,
        fallback=["bash", "-lc", "echo '== installed'; echo '== upgradable'; echo '== state'; echo inactive"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not read package versions").strip())
    parsed = _parse(result.stdout)
    installed, upgradable = parsed["installed"], parsed["upgradable"]
    services = []
    for key, label, main, patterns in _services(installed):
        updates = [
            {"name": name, "from": installed[name], "to": upgradable[name]}
            for name in sorted(upgradable)
            if name in installed and any(fnmatch(name, pattern) for pattern in patterns)
        ]
        main_to = upgradable.get(main, "")
        installed_short, available_short = _shown(installed[main], main_to)
        services.append({
            "key": key,
            "label": label,
            "package": main,
            "installed": installed[main],
            "installed_short": installed_short,
            "available": main_to,
            "available_short": available_short,
            "updates": updates,
        })
    return {
        "services": services,
        "running": parsed["running"],
        "log": parsed["log"][-40:],
        "last_result": _last_result(parsed["log"]),
        "check_error": parsed["check_error"],
        "pending": sum(len(service["updates"]) for service in services),
    }


def run(keys: list[str] | None) -> dict:
    """Start updating the given services, or every service when keys is None."""
    current = status()
    if current["running"]:
        raise ValueError("A service update is already running")
    known = {service["key"]: service for service in current["services"]}
    if keys is not None:
        unknown = [key for key in keys if key not in known]
        if unknown:
            raise ValueError(f"Unknown service: {', '.join(unknown)}")
        chosen = [known[key] for key in keys]
    else:
        chosen = list(known.values())
    packages = sorted({update["name"] for service in chosen for update in service["updates"]})
    if not packages:
        raise ValueError("Already up to date")
    result = shell.privileged(
        "service-packages-upgrade",
        helper_args=packages,
        check=False,
        fallback=["bash", "-lc", "echo service updates need opanel-helper"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not start the update").strip())
    return {
        "started": True,
        "services": [service["key"] for service in chosen if service["updates"]],
        "packages": packages,
        "message": (result.stdout or "").strip(),
    }
