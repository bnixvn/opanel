import ipaddress
import json
import re
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.services.shell import CommandResult, shell

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PORT_RE = re.compile(r"^[0-9]{1,5}$")
PROTOCOLS = {"tcp", "udp"}
DEFAULT_PROTECTED_PORTS = {22, 80, 443, 465, 587}
UFW_NUMBERED_RULE_RE = re.compile(r"^\[\s*(\d+)\]\s+(.+?)\s{2,}(ALLOW|DENY|REJECT|LIMIT)\s+(IN|OUT)\s+(.+)$", re.I)
IPTABLES_NUMBERED_RULE_RE = re.compile(r"^\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+--\s+(\S+)\s+(\S+)\s+(.+)$", re.I)
PORT_SPEC_RE = re.compile(r"^(\d{1,5})/(tcp|udp)(?:\s+\(v6\))?$", re.I)
ZONE_COMMENT_RE = re.compile(r"(?:opanel|bpanel):(PanelZone|UserZone)", re.I)

CHAIN_INPUT = "OPANEL_INPUT"
CHAIN_USER = "OPANEL_USER"
CHAIN_BLOCKLIST = "OPANEL_BLOCKLIST"

RULES_FILE = Path("/var/lib/opanel/firewall/rules.json")
BLOCKLIST_URLS_FILE = Path("/var/lib/opanel/firewall-blocklists.urls")

IPSET_V4 = "opanel_blocklist4"
IPSET_V6 = "opanel_blocklist6"
IPSET_V4_NEW = "opanel_blocklist4_new"
IPSET_V6_NEW = "opanel_blocklist6_new"

PANEL_ZONE = "PanelZone"
USER_ZONE = "UserZone"


def _validate_protocol(protocol: str) -> str:
    value = (protocol or "tcp").strip().lower()
    if value not in PROTOCOLS:
        raise ValueError("Protocol must be tcp or udp")
    return value


def _validate_port(port: str | int) -> str:
    value = str(port).strip()
    if not PORT_RE.match(value):
        raise ValueError("Port must be a number from 1 to 65535")
    number = int(value)
    if number < 1 or number > 65535:
        raise ValueError("Port must be a number from 1 to 65535")
    return value


def _validate_network(network: str) -> str:
    value = network.strip()
    try:
        parsed = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValueError("IP must be a valid IPv4/IPv6 address or CIDR network") from exc
    return str(parsed)


def _strip_inline_comment(value: str) -> str:
    return re.sub(r"\s+#.*$", "", value).strip()


def _zone_from_values(*values: str) -> str | None:
    joined = " ".join(values)
    match = ZONE_COMMENT_RE.search(joined)
    if not match:
        return None
    return PANEL_ZONE if match.group(1).lower() == PANEL_ZONE.lower() else USER_ZONE


def _classify_rule_zone(action: str, rule_type: str, port: str, comment_zone: str | None, protected_ports: set[int]) -> tuple[str, bool]:
    if comment_zone:
        return comment_zone, comment_zone == PANEL_ZONE
    is_protected = False
    if rule_type == "port" and action == "ALLOW" and port:
        try:
            is_protected = int(port) in protected_ports
        except ValueError:
            pass
    return (PANEL_ZONE if is_protected else USER_ZONE), is_protected


def _parse_numbered_output(output: str) -> list[dict]:
    protected_ports = set(DEFAULT_PROTECTED_PORTS)
    try:
        protected_ports.add(int(settings.panel_port or 2222))
    except (TypeError, ValueError):
        pass

    result: list[dict] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("["):
            continue

        ufw_match = UFW_NUMBERED_RULE_RE.match(line)
        if ufw_match:
            number = int(ufw_match.group(1))
            to_field = _strip_inline_comment(ufw_match.group(2))
            action = ufw_match.group(3).upper()
            direction = ufw_match.group(4).upper()
            from_field = _strip_inline_comment(ufw_match.group(5))
            port_match = PORT_SPEC_RE.match(to_field)
            if port_match:
                port, protocol = port_match.group(1), port_match.group(2).lower()
                rule_type = "port"
                display_to = f"{port}/{protocol}"
            else:
                port, protocol = "", "tcp"
                rule_type = "ip"
                display_to = "Anywhere" if to_field.lower() == "anywhere" else to_field
            comment_zone = _zone_from_values(line)
            zone, is_protected = _classify_rule_zone(action, rule_type, port, comment_zone, protected_ports)
            result.append({
                "id": number,
                "number": number,
                "to": display_to,
                "action": action,
                "direction": direction,
                "from": from_field or "Anywhere",
                "zone": zone,
                "protected": is_protected,
            })
            continue

        iptables_match = IPTABLES_NUMBERED_RULE_RE.match(line)
        if iptables_match:
            number = int(iptables_match.group(1))
            target = iptables_match.group(2).upper()
            protocol = iptables_match.group(3).lower()
            source = _strip_inline_comment(iptables_match.group(4))
            extra = iptables_match.group(6)
            port_match = re.search(r"(?:dpt|dpts):(\d+)", extra)
            port = port_match.group(1) if port_match else ""
            rule_type = "port" if port else "ip"
            if target == "ACCEPT":
                action = "ALLOW"
            elif target in {"DROP", "REJECT"}:
                action = "DENY"
            elif target == "LIMIT":
                action = "LIMIT"
            else:
                action = target
            comment_zone = _zone_from_values(line, extra)
            zone, is_protected = _classify_rule_zone(action, rule_type, port, comment_zone, protected_ports)
            result.append({
                "id": number,
                "number": number,
                "to": f"{port}/{protocol}" if port else "Anywhere",
                "action": action,
                "direction": "IN",
                "from": source or "Anywhere",
                "zone": zone,
                "protected": is_protected,
            })

    return result


def _panel_port() -> int:
    try:
        return int(settings.panel_port or 2222)
    except (TypeError, ValueError):
        return 2222


# ---------------------------------------------------------------------------
# Persistent rule store
# ---------------------------------------------------------------------------
def _ensure_rules_dir() -> None:
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_rules() -> list[dict]:
    if not RULES_FILE.exists():
        return []
    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_rules(rules: list[dict]) -> None:
    _ensure_rules_dir()
    RULES_FILE.write_text(
        json.dumps(rules, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# iptables helpers
# ---------------------------------------------------------------------------
def _iptables(*args: str, check: bool = True) -> CommandResult:
    return shell.privileged(
        "iptables-run",
        helper_args=list(args),
        check=check,
        fallback=["iptables"] + list(args),
    )


def _ip6tables(*args: str, check: bool = True) -> CommandResult:
    return shell.privileged(
        "ip6tables-run",
        helper_args=list(args),
        check=check,
        fallback=["ip6tables"] + list(args),
    )


def _ipset(*args: str, check: bool = True) -> CommandResult:
    return shell.privileged(
        "ipset-run",
        helper_args=list(args),
        check=check,
        fallback=["ipset"] + list(args),
    )


# ---------------------------------------------------------------------------
# Status & rule parsing
# ---------------------------------------------------------------------------
def status() -> CommandResult:
    return shell.privileged(
        "iptables-status",
        check=False,
        fallback=[
            "bash", "-lc",
            (
                "echo '=== IPv4 ==='; "
                "iptables -L OPANEL_INPUT -n -v 2>/dev/null || echo 'Chain not found'; "
                "echo; echo '=== IPv6 ==='; "
                "ip6tables -L OPANEL_INPUT -n -v 2>/dev/null || echo 'Chain not found'"
            ),
        ],
    )


def is_enabled() -> bool:
    result = shell.privileged(
        "iptables-check-enabled",
        check=False,
        fallback=[
            "bash", "-lc",
            "iptables -L OPANEL_INPUT -n >/dev/null 2>&1 && echo yes || echo no",
        ],
    )
    return "yes" in (result.stdout or "").lower()


def parse_numbered_rules(output: str) -> list[dict]:
    """Parse persistent rules into unified list with zone/protected metadata."""
    rules = _read_rules()
    parsed_output = _parse_numbered_output(output)
    if parsed_output:
        return parsed_output
    result = []
    protected_ports = set(DEFAULT_PROTECTED_PORTS)
    try:
        protected_ports.add(int(settings.panel_port or 2222))
    except (TypeError, ValueError):
        pass

    for rule in rules:
        rule_id = rule.get("id", 0)
        action = (rule.get("action") or "allow").upper()
        network = rule.get("network", "")
        port = rule.get("port", "")
        protocol = rule.get("protocol", "tcp")
        rule_type = rule.get("type", "ip")

        is_protected = False
        if rule_type == "port" and action == "ALLOW" and port:
            try:
                if int(port) in protected_ports:
                    is_protected = True
            except ValueError:
                pass

        zone = PANEL_ZONE if is_protected else USER_ZONE

        if rule_type == "port":
            display_to = f"{port}/{protocol}"
            display_from = "Anywhere"
        else:
            display_to = "Anywhere"
            if port:
                display_to = f"{port}/{protocol}"
            display_from = network or "Anywhere"

        result.append({
            "id": rule_id,
            "number": rule_id,
            "to": display_to,
            "action": action,
            "direction": "IN",
            "from": display_from,
            "zone": zone,
            "protected": is_protected,
        })

    return result


# ---------------------------------------------------------------------------
# Enable / Disable / Reload
# ---------------------------------------------------------------------------
def enable() -> CommandResult:
    """Enable OPanel firewall: create chains, set defaults, restore user rules."""
    port = _panel_port()
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # Create chains
        f"iptables -N {CHAIN_INPUT} 2>/dev/null || true\n"
        f"iptables -N {CHAIN_USER} 2>/dev/null || true\n"
        f"iptables -N {CHAIN_BLOCKLIST} 2>/dev/null || true\n"
        f"ip6tables -N {CHAIN_INPUT} 2>/dev/null || true\n"
        f"ip6tables -N {CHAIN_USER} 2>/dev/null || true\n"
        f"ip6tables -N {CHAIN_BLOCKLIST} 2>/dev/null || true\n"
        # Flush for idempotency
        f"iptables -F {CHAIN_INPUT}\n"
        f"iptables -F {CHAIN_USER}\n"
        f"iptables -F {CHAIN_BLOCKLIST}\n"
        f"ip6tables -F {CHAIN_INPUT}\n"
        f"ip6tables -F {CHAIN_USER}\n"
        f"ip6tables -F {CHAIN_BLOCKLIST}\n"
        # Insert into INPUT
        f"iptables -C INPUT -j {CHAIN_INPUT} 2>/dev/null || iptables -I INPUT 1 -j {CHAIN_INPUT}\n"
        f"ip6tables -C INPUT -j {CHAIN_INPUT} 2>/dev/null || ip6tables -I INPUT 1 -j {CHAIN_INPUT}\n"
        # Established + loopback
        f"iptables -A {CHAIN_INPUT} -m state --state ESTABLISHED,RELATED -j ACCEPT\n"
        f"iptables -A {CHAIN_INPUT} -i lo -j ACCEPT\n"
        f"ip6tables -A {CHAIN_INPUT} -m state --state ESTABLISHED,RELATED -j ACCEPT\n"
        f"ip6tables -A {CHAIN_INPUT} -i lo -j ACCEPT\n"
        # Default port allowances
        f"for p in 22 80 443 {port} 465 587; do\n"
        f"  iptables -A {CHAIN_INPUT} -p tcp --dport $p -j ACCEPT\n"
        f"  ip6tables -A {CHAIN_INPUT} -p tcp --dport $p -j ACCEPT\n"
        "done\n"
        # Blocklist chain (ipset)
        f"iptables -A {CHAIN_BLOCKLIST} -m set --match-set {IPSET_V4} src -j DROP 2>/dev/null || true\n"
        f"ip6tables -A {CHAIN_BLOCKLIST} -m set --match-set {IPSET_V6} src -j DROP 2>/dev/null || true\n"
        # Chain ordering: blocklist -> user
        f"iptables -A {CHAIN_INPUT} -j {CHAIN_BLOCKLIST}\n"
        f"iptables -A {CHAIN_INPUT} -j {CHAIN_USER}\n"
        f"ip6tables -A {CHAIN_INPUT} -j {CHAIN_BLOCKLIST}\n"
        f"ip6tables -A {CHAIN_INPUT} -j {CHAIN_USER}\n"
        # Default policy accept (don't lock out)
        "iptables -P INPUT ACCEPT\n"
        "ip6tables -P INPUT ACCEPT\n"
        "echo 'OPanel firewall enabled'\n"
    )
    result = shell.privileged("iptables-enable", check=False, fallback=["bash", "-lc", script])
    _restore_user_rules()
    return result


def _restore_user_rules() -> None:
    """Re-apply all persistent rules to the live OPANEL_USER chain."""
    for rule in _read_rules():
        _apply_rule_to_iptables(rule)


def disable() -> CommandResult:
    """Disable OPanel firewall: remove chains from INPUT, flush and delete."""
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"iptables -D INPUT -j {CHAIN_INPUT} 2>/dev/null || true\n"
        f"ip6tables -D INPUT -j {CHAIN_INPUT} 2>/dev/null || true\n"
        f"for chain in {CHAIN_INPUT} {CHAIN_USER} {CHAIN_BLOCKLIST}; do\n"
        '  iptables -F "$chain" 2>/dev/null || true\n'
        '  iptables -X "$chain" 2>/dev/null || true\n'
        '  ip6tables -F "$chain" 2>/dev/null || true\n'
        '  ip6tables -X "$chain" 2>/dev/null || true\n'
        "done\n"
        "iptables -P INPUT ACCEPT\n"
        "ip6tables -P INPUT ACCEPT\n"
        "echo 'OPanel firewall disabled'\n"
    )
    return shell.privileged("iptables-disable", check=False, fallback=["bash", "-lc", script])


def reload() -> CommandResult:
    """Reload: disable then re-enable with current rules."""
    disable()
    return enable()


# ---------------------------------------------------------------------------
# User rules (declarative, stored in JSON)
# ---------------------------------------------------------------------------
def _next_rule_id(rules: list[dict]) -> int:
    if not rules:
        return 1
    return max(r.get("id", 0) for r in rules) + 1


def _apply_rule_to_iptables(rule: dict) -> None:
    action = rule.get("action", "allow").upper()
    network = rule.get("network", "")
    port = rule.get("port", "")
    protocol = rule.get("protocol", "tcp")
    if not network and rule.get("type") == "ip":
        return
    is_v6 = network and ":" in network
    target = "ACCEPT" if action == "ALLOW" else "DROP"

    if rule.get("type") == "port":
        _iptables("-A", CHAIN_USER, "-p", protocol, "--dport", str(port), "-j", "ACCEPT", check=False)
        _ip6tables("-A", CHAIN_USER, "-p", protocol, "--dport", str(port), "-j", "ACCEPT", check=False)
    elif network:
        cmd_fn = _ip6tables if is_v6 else _iptables
        argv = ["-A", CHAIN_USER]
        if port:
            argv += ["-p", protocol, "--dport", str(port)]
        argv += ["-s", network, "-j", target]
        cmd_fn(*argv, check=False)


def _remove_rule_from_iptables(rule: dict) -> None:
    action = rule.get("action", "allow").upper()
    network = rule.get("network", "")
    port = rule.get("port", "")
    protocol = rule.get("protocol", "tcp")
    if not network and rule.get("type") == "ip":
        return
    is_v6 = network and ":" in network
    target = "ACCEPT" if action == "ALLOW" else "DROP"

    if rule.get("type") == "port":
        _iptables("-D", CHAIN_USER, "-p", protocol, "--dport", str(port), "-j", "ACCEPT", check=False)
        _ip6tables("-D", CHAIN_USER, "-p", protocol, "--dport", str(port), "-j", "ACCEPT", check=False)
    elif network:
        cmd_fn = _ip6tables if is_v6 else _iptables
        argv = ["-D", CHAIN_USER]
        if port:
            argv += ["-p", protocol, "--dport", str(port)]
        argv += ["-s", network, "-j", target]
        cmd_fn(*argv, check=False)


def allow_port(port: str | int, protocol: str = "tcp") -> CommandResult:
    clean_port = _validate_port(port)
    clean_protocol = _validate_protocol(protocol)
    rules = _read_rules()
    rule = {
        "id": _next_rule_id(rules),
        "action": "allow",
        "type": "port",
        "port": clean_port,
        "protocol": clean_protocol,
        "network": "",
    }
    rules.append(rule)
    _write_rules(rules)
    _iptables("-A", CHAIN_USER, "-p", clean_protocol, "--dport", clean_port, "-j", "ACCEPT", check=False)
    _ip6tables("-A", CHAIN_USER, "-p", clean_protocol, "--dport", clean_port, "-j", "ACCEPT", check=False)
    return CommandResult(command=f"allow port {clean_port}/{clean_protocol}", returncode=0, stdout="Port allowed", stderr="")


def allow_ip(network: str, port: Optional[str | int] = None, protocol: str = "tcp") -> CommandResult:
    clean_network = _validate_network(network)
    clean_protocol = _validate_protocol(protocol)
    rules = _read_rules()
    rule = {
        "id": _next_rule_id(rules),
        "action": "allow",
        "type": "ip",
        "network": clean_network,
        "protocol": clean_protocol,
    }
    if port:
        rule["port"] = _validate_port(port)
    rules.append(rule)
    _write_rules(rules)
    _apply_rule_to_iptables(rule)
    return CommandResult(command=f"allow ip {clean_network}", returncode=0, stdout="IP allowed", stderr="")


def block_ip(network: str, port: Optional[str | int] = None, protocol: str = "tcp") -> CommandResult:
    clean_network = _validate_network(network)
    clean_protocol = _validate_protocol(protocol)
    rules = _read_rules()
    rule = {
        "id": _next_rule_id(rules),
        "action": "deny",
        "type": "ip",
        "network": clean_network,
        "protocol": clean_protocol,
    }
    if port:
        rule["port"] = _validate_port(port)
    rules.append(rule)
    _write_rules(rules)
    _apply_rule_to_iptables(rule)
    return CommandResult(command=f"block ip {clean_network}", returncode=0, stdout="IP blocked", stderr="")


def delete_rule(rule_id: int) -> CommandResult:
    if rule_id < 1:
        raise ValueError("Rule ID must be greater than 0")
    rules = _read_rules()
    target = next((r for r in rules if r.get("id") == rule_id), None)
    if not target:
        raise ValueError(f"Rule {rule_id} not found")
    # Check if protected
    rule_type = target.get("type", "ip")
    action = (target.get("action") or "").upper()
    port = target.get("port", "")
    protected_ports = set(DEFAULT_PROTECTED_PORTS)
    try:
        protected_ports.add(int(settings.panel_port or 2222))
    except (TypeError, ValueError):
        pass
    if rule_type == "port" and action == "ALLOW" and port:
        try:
            if int(port) in protected_ports:
                raise ValueError("Default panel, mail, web, and SSH firewall rules cannot be deleted")
        except (ValueError, TypeError):
            pass
    _remove_rule_from_iptables(target)
    rules = [r for r in rules if r.get("id") != rule_id]
    _write_rules(rules)
    return CommandResult(command=f"delete rule {rule_id}", returncode=0, stdout=f"Rule {rule_id} deleted", stderr="")


def list_rules() -> list[dict]:
    return _read_rules()


# ---------------------------------------------------------------------------
# Blocklist (ipset + iptables)
# ---------------------------------------------------------------------------
def blocklists() -> CommandResult:
    return shell.privileged(
        "iptables-blocklist-status",
        check=False,
        fallback=[
            "bash", "-lc",
            (
                f"echo 'URLs:'; cat {BLOCKLIST_URLS_FILE} 2>/dev/null || true; "
                "echo; echo 'Engine:'; echo '  iptables+ipset'"
            ),
        ],
    )


def add_blocklist_url(url: str) -> CommandResult:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    _ensure_rules_dir()
    existing = ""
    if BLOCKLIST_URLS_FILE.exists():
        existing = BLOCKLIST_URLS_FILE.read_text(encoding="utf-8")
    if url in existing.splitlines():
        return CommandResult(command="add blocklist url", returncode=0, stdout="URL already added", stderr="")
    with open(BLOCKLIST_URLS_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")
    return CommandResult(command="add blocklist url", returncode=0, stdout="URL added", stderr="")


def delete_blocklist_url(url: str) -> CommandResult:
    if not BLOCKLIST_URLS_FILE.exists():
        return CommandResult(command="delete blocklist url", returncode=0, stdout="URL not found", stderr="")
    lines = BLOCKLIST_URLS_FILE.read_text(encoding="utf-8").splitlines()
    new_lines = [line for line in lines if line.strip() != url.strip()]
    suffix = "\n" if new_lines else ""
    BLOCKLIST_URLS_FILE.write_text("\n".join(new_lines) + suffix, encoding="utf-8")
    return CommandResult(command="delete blocklist url", returncode=0, stdout="URL removed", stderr="")


def update_blocklists() -> CommandResult:
    return shell.privileged(
        "iptables-blocklist-run",
        check=False,
        fallback=["bash", "-lc", "echo 'Blocklist update triggered'"],
    )
