#!/usr/bin/env bash
# /usr/local/sbin/opanel-helper
#
# Root-privileged trampoline for the OPanel API daemon.
# This is the ONLY code that runs as root for the daemon.
# Installed by install.sh as root:root mode 0750, callable only by user
# 'opanel' through sudo (see /etc/sudoers.d/opanel).
#
# Every operation here is the trust boundary. Validate aggressively.

set -euo pipefail

if [[ "${SUDO_USER:-}" != "opanel" ]]; then
  echo "opanel-helper must be invoked by user 'opanel' via sudo" >&2
  exit 2
fi

# Reset PATH so an attacker cannot ship a shadow binary in opanel's PATH.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

ALLOWED_SERVICES=(lsws mariadb redis-server opanel-api)
ALLOWED_ACTIONS=(start stop restart reload status is-active is-enabled)
HOME_ROOT="/home"
OLS_HTTPD_CONF="/usr/local/lsws/conf/httpd_config.conf"
OLS_VHOSTS_DIR="/usr/local/lsws/conf/opanel/vhosts"
# Per-site PHP error logs. Not under /var/log/openlitespeed: that is 2770 so
# tenants cannot read each other's 0644 access logs, which also kept every
# site's PHP (running as the site user) out of its own php_error.log. See
# ensure_php_log_dir.
PHP_LOG_ROOT="/var/log/opanel-php"
PHP_CONF_DIRS=(/usr/local/lsws/lsphp{83,84}/etc/php.d)
opanel_SITES_GROUP="opanel-sites"
opanel_SFTP_GROUP="opanel-sftp"
APP_DIR="/opt/opanel"
ENV_FILE="${APP_DIR}/backend/.env"
DEFAULT_PANEL_PORT="2222"
SOURCE_DIR="/opt/opanel-source"
UPDATE_SCRIPT="/usr/local/sbin/opanel-update"
opanel_DATA_DIR="/var/lib/opanel"
FIREWALL_BLOCKLIST_URLS="${opanel_DATA_DIR}/firewall-blocklists.urls"
FIREWALL_BLOCKLIST_WORK="${opanel_DATA_DIR}/firewall-blocklists.current"
BLOCKLIST_DIR="${opanel_DATA_DIR}/firewall"
BLOCKLIST_IPSET_V4="opanel_blocklist4"
BLOCKLIST_IPSET_V6="opanel_blocklist6"
OLS_CUSTOM_DIR="/usr/local/lsws/conf/opanel/custom"
LSPHP_DEFAULT_WORKER_MB=128
LSPHP_DEFAULT_REQUEST_TERMINATE_TIMEOUT=300
MARIADB_TUNING_CONF="/etc/mysql/mariadb.conf.d/90-opanel-tuning.cnf"

deny() { echo "opanel-helper: $*" >&2; exit 1; }

restart_openlitespeed() {
  ensure_lshttpd_runtime_dir
  if systemctl cat lshttpd.service >/dev/null 2>&1; then
    systemctl restart lshttpd.service
  else
    /usr/local/lsws/bin/lswsctrl restart
  fi
}

ensure_opanel_data_dir() {
  install -d -o opanel -g opanel -m 0750 "$opanel_DATA_DIR"
}

ensure_ols_conf_dir_writable() {
  ensure_sites_group
  install -d -o root -g root -m 0755 "$BLOCKLIST_DIR"
  # 2770, not 2775. The per-domain subdirectory is 0750, but OpenLiteSpeed's own
  # vhost logs sit at this top level as <domain>.access.log / <domain>.error.log
  # and nothing sets their mode, so OLS creates them 0644 at its umask. With
  # "other" able to traverse here, any site's Linux user could read every other
  # tenant's access log -- full request lines, so query strings, password-reset
  # tokens and API keys. The panel reads these as root via read_site_log, so no
  # "other" access is needed. Verified on a live box before the change.
  install -d -o www-data -g "$opanel_SITES_GROUP" -m 2770 /var/log/openlitespeed
  chmod 2770 /var/log/openlitespeed 2>/dev/null || true
  ensure_lshttpd_runtime_dir
  chmod g+s /var/log/openlitespeed 2>/dev/null || true
  if getent group opanel >/dev/null 2>&1; then
    install -d -o root -g opanel -m 2775 "$OLS_VHOSTS_DIR"
    install -d -o root -g opanel -m 2775 "$OLS_CUSTOM_DIR"
    chmod g+s "$OLS_VHOSTS_DIR" 2>/dev/null || true
    chmod g+s "$OLS_CUSTOM_DIR" 2>/dev/null || true
  else
    install -d -o root -g root -m 0755 "$OLS_VHOSTS_DIR"
    install -d -o root -g root -m 0755 "$OLS_CUSTOM_DIR"
  fi
}

ensure_lshttpd_runtime_dir() {
  ensure_sites_group
  # 2770, not 2775. This tree is OpenLiteSpeed's swappingDir: request bodies
  # too large for memory are spilled here, so a world-readable chain meant any
  # site's Linux user could read another tenant's in-flight POST bodies and
  # uploads. Verified on a live box: the directories were 2775 and the .lsb
  # files 0664, in a /tmp/lshttpd that "other" could traverse. Only www-data
  # (the server) and opanel-sites need access.
  install -d -o www-data -g "$opanel_SITES_GROUP" -m 2770 /tmp/lshttpd /tmp/lshttpd/swap
  chmod 2770 /tmp/lshttpd /tmp/lshttpd/swap 2>/dev/null || true
  chmod g+s /tmp/lshttpd 2>/dev/null || true
  if [[ -d /tmp/lshttpd/swap ]]; then
    chown -R www-data:"$opanel_SITES_GROUP" /tmp/lshttpd/swap 2>/dev/null || true
    find /tmp/lshttpd/swap -type d -exec chmod 2770 {} + 2>/dev/null || true
    find /tmp/lshttpd/swap -type f -exec chmod 0660 {} + 2>/dev/null || true
  fi
  chown www-data:"$opanel_SITES_GROUP" /tmp/lshttpd/lsphp*.sock /tmp/lshttpd/lsphp*.sock.pid 2>/dev/null || true
  chmod 0664 /tmp/lshttpd/lsphp*.sock.pid 2>/dev/null || true
}

fix_phpmyadmin_permissions() {
  # Private session store for the phpMyAdmin sign-on handshake. It used to
  # write into /var/lib/php/sessions, the same parent every site's per-user
  # session directory lives under, and those sessions carry a cleartext
  # database user and password.
  install -d -o www-data -g www-data -m 0700 /var/lib/php/pma-sessions 2>/dev/null || true
  chown www-data:www-data /var/lib/php/pma-sessions 2>/dev/null || true
  chmod 0700 /var/lib/php/pma-sessions 2>/dev/null || true
  # All of these are 0640 root:opanel-sites. www-data is a member of
  # opanel-sites (install.sh, ensure_sites_group and update.sh all put it
  # there), so group-readable is enough for the PHP that has to read them --
  # the signon files in particular carry the X-OPanel-Signon-Secret that gates
  # an endpoint returning any account's database credentials, and they were
  # world-readable, which every site's Linux user could exploit.
  local file
  for file in \
    /etc/phpmyadmin/conf.d/opanel-signon.php \
    /etc/phpmyadmin/config.inc.php \
    /usr/share/phpmyadmin/opanel-signon.php \
    /etc/phpmyadmin/config-db.php \
    /var/lib/phpmyadmin/blowfish_secret.inc.php; do
    [[ -e "$file" ]] || continue
    chgrp "$opanel_SITES_GROUP" "$file" 2>/dev/null || true
    chmod 0640 "$file" 2>/dev/null || true
  done
}

ols_disable_conflicting_apache() {
  if systemctl list-unit-files apache2.service >/dev/null 2>&1; then
    systemctl disable --now apache2 >/dev/null 2>&1 || true
  fi
}

ensure_ols_modsecurity_enabled() {
  [[ -f /usr/local/lsws/modules/mod_security.so ]] || return 1
  python3 - "$OLS_HTTPD_CONF" <<'PY'
import pathlib
import re
import sys

conf = pathlib.Path(sys.argv[1])
if conf.exists():
    text = conf.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
else:
    text = ""
text = re.sub(r"(?ms)^# OPANEL managed ModSecurity BEGIN\n.*?^# OPANEL managed ModSecurity END\n?", "", text)
block = (
    "# OPANEL managed ModSecurity BEGIN\n"
    "module mod_security {\n"
    "    modsecurity             on\n"
    "    ls_enabled              1\n"
    "}\n"
    "# OPANEL managed ModSecurity END\n\n"
)
marker = "# OPanel managed vhosts BEGIN"
pos = text.find(marker)
if pos >= 0:
    text = text[:pos] + block + text[pos:]
else:
    text = text.rstrip() + "\n\n" + block
conf.write_text(text, encoding="utf-8")
PY
}

ols_sync_main_config() {
  ensure_ols_conf_dir_writable
  install -d -o root -g root -m 0755 /usr/local/lsws/conf/opanel
  ols_disable_conflicting_apache
  ensure_ols_modsecurity_enabled >/dev/null 2>&1 || true
  python3 - "$OLS_HTTPD_CONF" "$OLS_VHOSTS_DIR" "$ENV_FILE" <<'PY'
import pathlib
import re
import sys

conf = pathlib.Path(sys.argv[1])
vhosts_dir = pathlib.Path(sys.argv[2])
env_file = pathlib.Path(sys.argv[3])
tools_conf_name = "00-opanel-tools.conf"
tools_conf = vhosts_dir / tools_conf_name
# The Email addon's webmail, proxied to 127.0.0.1 and served on its own port
# for every hostname that reaches the box.
webmail_conf_name = "00-opanel-webmail.conf"
webmail_conf = vhosts_dir / webmail_conf_name
domain_re = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
ipv4_re = re.compile(r"^(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3}$")
settings_file = pathlib.Path("/var/lib/opanel/panel-settings.json")


def host_has_global_ipv6() -> bool:
    """A global (scope 0) address on something other than loopback."""
    try:
        for line in pathlib.Path("/proc/net/if_inet6").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 6 and fields[3] == "00" and fields[5] != "lo":
                return True
    except OSError:
        return False
    return False


def ipv6_enabled() -> bool:
    """Panel setting wins, but the address still has to exist.

    A listener on an address family the kernel no longer has stops
    OpenLiteSpeed from starting at all, which would take every site down over
    a setting nobody re-read, so a stale "on" flag simply produces no IPv6
    listener.
    """
    if not host_has_global_ipv6():
        return False
    try:
        import json

        stored = json.loads(settings_file.read_text(encoding="utf-8"))
        if isinstance(stored, dict) and "ipv6_enabled" in stored:
            return bool(stored["ipv6_enabled"])
    except (OSError, ValueError):
        pass
    # Never asked: a box that has IPv6 serves it from the first boot.
    return True


def env_get(key: str) -> str:
    if not env_file.is_file():
        return ""
    prefix = f"{key}="
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""

def normalized_host(value: str) -> str:
    value = value.strip().lower()
    if not value:
        return ""
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0]
    if ":" in value:
        value = value.rsplit(":", 1)[0]
    if domain_re.fullmatch(value) or ipv4_re.fullmatch(value):
        return value
    return ""

if conf.exists():
    text = conf.read_text(encoding="utf-8", errors="replace")
else:
    text = ""
text = text.replace("\r\n", "\n")
if re.search(r"(?m)^\s*user\s+", text):
    text = re.sub(r"(?m)^\s*user\s+.*$", "user                             www-data", text, count=1)
else:
    text = "user                             www-data\n" + text
if re.search(r"(?m)^\s*group\s+", text):
    text = re.sub(r"(?m)^\s*group\s+.*$", "group                            opanel-sites", text, count=1)
else:
    text = "group                            opanel-sites\n" + text

def remove_named_block(source: str, directive: str, name: str) -> str:
    pattern = re.compile(rf"(?ms)^[ \t]*{re.escape(directive)}[ \t]+{re.escape(name)}[ \t]*\{{.*?^[ \t]*\}}[ \t]*\n?")
    return pattern.sub("", source)

for block_name in ("opanel_http", "opanel_https", "opanel_http6", "opanel_https6",
                   "opanel_webmail", "opanel_webmail6"):
    text = remove_named_block(text, "listener", block_name)
text = re.sub(r"(?ms)^# OPanel managed vhosts BEGIN\n.*?^# OPanel managed vhosts END\n?", "", text)

sites = []
if vhosts_dir.is_dir():
    for vhost_file in sorted(vhosts_dir.glob("*/vhost.conf")):
        domain = vhost_file.parent.name.lower()
        if not domain_re.fullmatch(domain):
            continue
        content = vhost_file.read_text(encoding="utf-8", errors="replace")
        hosts = [domain]
        for match in re.finditer(r"(?m)^\s*vh(?:Domain|Aliases)\s+(.+?)\s*$", content):
            for host in re.split(r"[,\s]+", match.group(1).strip()):
                host = host.strip().lower()
                if domain_re.fullmatch(host) and host not in hosts:
                    hosts.append(host)
        cert_match = re.search(r"(?m)^\s*certFile\s+(.+?)\s*$", content)
        key_match = re.search(r"(?m)^\s*keyFile\s+(.+?)\s*$", content)
        has_ssl = False
        if cert_match and key_match:
            cert_path = pathlib.Path(cert_match.group(1).strip())
            key_path = pathlib.Path(key_match.group(1).strip())
            has_ssl = cert_path.is_file() and key_path.is_file()
        sites.append((domain, hosts, has_ssl))

panel_hosts = []
for raw_host in (env_get("PANEL_DOMAIN"), env_get("PANEL_URL")):
    host = normalized_host(raw_host)
    if host and host not in panel_hosts:
        panel_hosts.append(host)
site_hosts = {host for _domain, hosts, _has_ssl in sites for host in hosts}
tools_hosts = [host for host in panel_hosts if host not in site_hosts]
include_tools_vhost = tools_conf.is_file() and bool(tools_hosts)

managed = ["# OPanel managed vhosts BEGIN"]
if include_tools_vhost:
    managed.extend([
        "virtualHost opanel_tools {",
        "    vhRoot                   conf/opanel/vhosts/",
        "    allowSymbolLink          1",
        "    enableScript             1",
        "    restrained               1",
        f"    configFile               conf/opanel/vhosts/{tools_conf_name}",
        "}",
        "",
    ])
if webmail_conf.is_file():
    managed.extend([
        "virtualHost opanel_webmail {",
        "    vhRoot                   conf/opanel/vhosts/",
        "    allowSymbolLink          1",
        "    enableScript             1",
        "    restrained               1",
        f"    configFile               conf/opanel/vhosts/{webmail_conf_name}",
        "}",
        "",
    ])
for domain, _hosts, _has_ssl in sites:
    managed.extend([
        f"virtualHost {domain} {{",
        f"    vhRoot                   conf/opanel/vhosts/{domain}/",
        "    allowSymbolLink          1",
        "    enableScript             1",
        "    restrained               1",
        "    setUIDMode               2",
        f"    configFile               conf/opanel/vhosts/{domain}/vhost.conf",
        "}",
        "",
    ])

# Read panel TLS cert from tools vhost for the HTTPS listener default
_tools_cert = pathlib.Path("/etc/ssl/certs/ssl-cert-snakeoil.pem")
_tools_key = pathlib.Path("/etc/ssl/private/ssl-cert-snakeoil.key")
if tools_conf.is_file():
    _tc = tools_conf.read_text(encoding="utf-8", errors="replace")
    _cm = re.search(r"(?m)^\s*certFile\s+(.+?)\s*$", _tc)
    _km = re.search(r"(?m)^\s*keyFile\s+(.+?)\s*$", _tc)
    if _cm and _km:
        _cp = pathlib.Path(_cm.group(1).strip())
        _kp = pathlib.Path(_km.group(1).strip())
        if _cp.is_file() and _kp.is_file():
            _tools_cert, _tools_key = _cp, _kp

def listener_block(name: str, address: str, secure: bool, include_ssl_sites: bool) -> list[str]:
    lines = [f"listener {name} {{", f"    address                  {address}", f"    secure                   {1 if secure else 0}"]
    if secure:
        lines.extend([
            f"    keyFile                 {_tools_key}",
            f"    certFile                {_tools_cert}",
            "    certChain               1",
            "    enableSpdy              16",
            "    enableQuic              1",
        ])
    if include_tools_vhost:
        for host in tools_hosts:
            lines.append(f"    map                      opanel_tools {host}")
    for domain, hosts, has_ssl in sites:
        if include_ssl_sites and not has_ssl:
            continue
        lines.append(f"    map                      {domain} {', '.join(hosts)}")
    lines.append("}")
    lines.append("")
    return lines

def webmail_listener(name: str, address: str) -> list[str]:
    return [
        f"listener {name} {{",
        f"    address                  {address}",
        "    secure                   1",
        f"    keyFile                 {_tools_key}",
        f"    certFile                {_tools_cert}",
        "    certChain               1",
        "    map                      opanel_webmail *",
        "}",
        "",
    ]

managed.extend(listener_block("opanel_http", "*:80", False, False))
managed.extend(listener_block("opanel_https", "*:443", True, True))
if ipv6_enabled():
    # "*" is IPv4-only in OpenLiteSpeed; [ANY] is the IPv6 wildcard. Sites are
    # mapped into both, so every vhost answers on either protocol.
    managed.extend(listener_block("opanel_http6", "[ANY]:80", False, False))
    managed.extend(listener_block("opanel_https6", "[ANY]:443", True, True))
if webmail_conf.is_file():
    managed.extend(webmail_listener("opanel_webmail", "*:2096"))
    if ipv6_enabled():
        managed.extend(webmail_listener("opanel_webmail6", "[ANY]:2096"))
managed.append("# OPanel managed vhosts END")
managed.append("")

new_text = text.rstrip() + "\n\n" + "\n".join(managed)
conf.parent.mkdir(parents=True, exist_ok=True)
if conf.exists() and conf.read_text(encoding="utf-8", errors="replace") == new_text:
    raise SystemExit(0)
backup = conf.with_suffix(conf.suffix + ".opanel.bak")
if conf.exists():
    backup.write_text(conf.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
conf.write_text(new_text, encoding="utf-8")
PY
}

file_has_nul() {
  local path="$1"
  python3 - "$path" <<'PY'
import sys

with open(sys.argv[1], "rb") as handle:
    data = handle.read()
sys.exit(0 if b"\0" in data else 1)
PY
}

env_get() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

env_set() {
  local key="$1" value="$2" escaped
  [[ -f "$ENV_FILE" ]] || deny "$ENV_FILE not found"
  escaped="$(printf '%s' "$value" | sed -e 's/[&|]/\\&/g')"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

detect_ip() {
  hostname -I 2>/dev/null | awk '{print $1}' || true
}

is_ipv4() {
  local value="$1" part
  local -a parts
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS=. read -r -a parts <<<"$value"
  for part in "${parts[@]}"; do
    (( 10#$part >= 0 && 10#$part <= 255 )) || return 1
  done
}

is_domain() {
  ! is_ipv4 "$1" && [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]
}

panel_url_scheme() {
  local url
  url="$(env_get PANEL_URL)"
  case "$url" in
    https://*) echo "https" ;;
    *) echo "http" ;;
  esac
}

panel_tls_enabled() {
  local cert key
  [[ "$(panel_url_scheme)" == "https" ]] || return 1
  cert="$(env_get PANEL_SSL_CERT)"; key="$(env_get PANEL_SSL_KEY)"
  [[ -n "$cert" && -n "$key" && -f "$cert" && -f "$key" ]]
}

require_panel_scheme() {
  [[ "$1" == "http" || "$1" == "https" ]] || deny "invalid panel scheme: $1"
}

require_panel_host() {
  local host="$1"
  if is_domain "$host" || is_ipv4 "$host" || [[ "$host" == "localhost" ]]; then
    return 0
  fi
  deny "invalid panel host: $host"
}

set_outbound_ipv4_preference() {
  # Serving IPv6 says nothing about being able to reach the internet over it.
  # Plenty of VPS images hand out an address with no working route, and many
  # providers block outbound 25/587 on IPv6 -- while glibc starts preferring
  # IPv6 the moment an address exists, so mail to any relay with an AAAA
  # record begins to fail. Pinning outbound to IPv4 leaves delivery exactly as
  # it was before the switch; inbound still answers on both families.
  local mode="$1" tmp
  [[ -f /etc/gai.conf ]] || : >/etc/gai.conf
  tmp="$(mktemp /tmp/opanel-gai.XXXXXX)" || return 0
  awk '$0 == "# OPanel BEGIN" { skip = 1 } skip == 0 { print } $0 == "# OPanel END" { skip = 0 }'     /etc/gai.conf >"$tmp" 2>/dev/null || cp /etc/gai.conf "$tmp"
  if [[ "$mode" == "on" ]]; then
    printf '%s\n' "# OPanel BEGIN" "precedence ::ffff:0:0/96  100" "# OPanel END" >>"$tmp"
  fi
  install -o root -g root -m 0644 "$tmp" /etc/gai.conf
  rm -f "$tmp"
}

allow_panel_port() {
  local port="$1"
  iptables_panel_allow_port "$port"
}

iptables_panel_allow_port() {
  local port="$1" binary
  require_port "$port"
  iptables_ensure_opanel_chains
  # Remove any existing opanel panel-zone rules for this port first
  iptables_panel_delete_port_rules "$port" "opanel:PanelZone"
  # Both families: a port opened only for IPv4 is a closed port to every
  # visitor whose DNS answer was an AAAA record.
  for binary in iptables ip6tables; do
    "$binary" -I OPANEL_INPUT 1 -p tcp --dport "$port" -j ACCEPT -m comment --comment "opanel:PanelZone" 2>/dev/null \
      || "$binary" -I OPANEL_INPUT 1 -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
      || true
  done
}

firewall_persist_rules() {
  install -d -o root -g root -m 0755 /etc/iptables
  iptables-save >/etc/iptables/rules.v4 2>/dev/null || true
  ip6tables-save >/etc/iptables/rules.v6 2>/dev/null || true
}

iptables_refresh_standard_ports() {
  # SSH, web, and the three mail ports. Re-applied whenever IPv6 is switched
  # on, because a chain built before the box had IPv6 only ever got the IPv4
  # half -- which reads to a visitor arriving over IPv6 as a closed port.
  local port p
  port="$(env_get PANEL_PORT)"
  port="${port:-$DEFAULT_PANEL_PORT}"
  local -a ports=(22 25 80 443 465 587 "$port")
  if mail_installed; then ports+=("${MAIL_EXTRA_PORTS[@]}"); fi
  for p in "${ports[@]}"; do
    iptables_panel_allow_port "$p" 2>/dev/null || true
  done
  if dns_installed; then dns_open_ports; fi
}

iptables_panel_delete_port_rules() {
  local port="$1" comment="${2:-}" binary num line_nums
  # Both families, so re-applying a port cannot leave a duplicate IPv6 rule
  # behind and closing one really closes it.
  for binary in iptables ip6tables; do
    line_nums="$("$binary" -L OPANEL_INPUT -n --line-numbers 2>/dev/null \
      | awk -v port="$port" -v comment="$comment" '
          $0 ~ ("tcp dpt:" port "([^0-9]|$)") {
            if (comment == "" || $0 ~ comment) {
              gsub(/[^0-9]/, "", $1)
              if ($1 != "") print $1
            }
          }
        ' | sort -rn)"
    for num in $line_nums; do
      [[ -n "$num" ]] || continue
      "$binary" -D OPANEL_INPUT "$num" 2>/dev/null || true
    done
  done
}

iptables_panel_delete_commented_rules() {
  local comment="$1"
  local line_nums
  line_nums="$(iptables -L OPANEL_INPUT -n --line-numbers 2>/dev/null \
    | awk -v comment="$comment" '
        $0 ~ comment {
          gsub(/[^0-9]/, "", $1)
          if ($1 != "") print $1
        }
      ' | sort -rn)"
  local num
  for num in $line_nums; do
    [[ -n "$num" ]] || continue
    iptables -D OPANEL_INPUT "$num" 2>/dev/null || true
  done
}

iptables_ensure_opanel_chains() {
  iptables -N OPANEL_INPUT 2>/dev/null || true
  iptables -N OPANEL_USER 2>/dev/null || true
  iptables -N OPANEL_BLOCKLIST 2>/dev/null || true
  ip6tables -N OPANEL_INPUT 2>/dev/null || true
  ip6tables -N OPANEL_USER 2>/dev/null || true
  ip6tables -N OPANEL_BLOCKLIST 2>/dev/null || true
}

iptables_flush_managed_chains() {
  iptables_ensure_opanel_chains
  iptables -F OPANEL_INPUT 2>/dev/null || true
  iptables -F OPANEL_USER 2>/dev/null || true
  iptables -F OPANEL_BLOCKLIST 2>/dev/null || true
  ip6tables -F OPANEL_INPUT 2>/dev/null || true
  ip6tables -F OPANEL_USER 2>/dev/null || true
  ip6tables -F OPANEL_BLOCKLIST 2>/dev/null || true
}

iptables_insert_managed_jumps() {
  # Order matters: OPANEL_INPUT accepts the default ports from any source, and
  # an ACCEPT inside a user chain ends the traversal. With it ahead of
  # OPANEL_USER an admin's "block this IP" never ran for ports 22/80/443/the
  # panel -- which is every port an attacker uses. Admin rules go first.
  iptables -C INPUT -j OPANEL_BLOCKLIST 2>/dev/null || iptables -I INPUT 1 -j OPANEL_BLOCKLIST
  iptables -C INPUT -j OPANEL_USER 2>/dev/null || iptables -I INPUT 2 -j OPANEL_USER
  iptables -C INPUT -j OPANEL_INPUT 2>/dev/null || iptables -I INPUT 3 -j OPANEL_INPUT
  ip6tables -C INPUT -j OPANEL_BLOCKLIST 2>/dev/null || ip6tables -I INPUT 1 -j OPANEL_BLOCKLIST
  ip6tables -C INPUT -j OPANEL_USER 2>/dev/null || ip6tables -I INPUT 2 -j OPANEL_USER
  ip6tables -C INPUT -j OPANEL_INPUT 2>/dev/null || ip6tables -I INPUT 3 -j OPANEL_INPUT
}

iptables_reorder_managed_jumps() {
  # Existing installs already have the jumps in the old order; -C above would
  # find them and leave it. Drop and re-add so the fix reaches them too.
  local binary
  for binary in iptables ip6tables; do
    "$binary" -D INPUT -j OPANEL_BLOCKLIST 2>/dev/null || true
    "$binary" -D INPUT -j OPANEL_USER 2>/dev/null || true
    "$binary" -D INPUT -j OPANEL_INPUT 2>/dev/null || true
  done
  iptables_insert_managed_jumps
}

iptables_add_default_allowances() {
  local port p
  port="$(env_get PANEL_PORT)"
  port="${port:-$DEFAULT_PANEL_PORT}"
  local -a ports=(22 25 80 443 465 587 "$port")
  if mail_installed; then ports+=("${MAIL_EXTRA_PORTS[@]}"); fi
  if dns_installed; then dns_open_ports; fi
  for p in "${ports[@]}"; do
    require_port "$p"
    iptables -A OPANEL_INPUT -p tcp --dport "$p" -j ACCEPT -m comment --comment "opanel:PanelZone" 2>/dev/null \
      || iptables -A OPANEL_INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
      || true
    ip6tables -A OPANEL_INPUT -p tcp --dport "$p" -j ACCEPT -m comment --comment "opanel:PanelZone" 2>/dev/null \
      || ip6tables -A OPANEL_INPUT -p tcp --dport "$p" -j ACCEPT 2>/dev/null \
      || true
  done
  iptables -A OPANEL_INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  iptables -A OPANEL_INPUT -i lo -j ACCEPT 2>/dev/null || true
  iptables -A OPANEL_INPUT -p icmp -j ACCEPT 2>/dev/null || true
  ip6tables -A OPANEL_INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
  ip6tables -A OPANEL_INPUT -i lo -j ACCEPT 2>/dev/null || true
  ip6tables -A OPANEL_INPUT -p ipv6-icmp -j ACCEPT 2>/dev/null || true
}

run_managed_iptables_command() {
  local binary="$1" op="${2:-}" chain="${3:-}" proto="" port="" network="" target=""
  local -a argv
  shift || true
  [[ $# -ge 2 ]] || deny "usage: ${binary}-run <-A|-D> OPANEL_USER ..."
  op="$1"; chain="$2"; shift 2
  [[ "$op" == "-A" || "$op" == "-D" ]] || deny "unsupported ${binary} operation: $op"
  [[ "$chain" == "OPANEL_USER" ]] || deny "unsupported ${binary} chain: $chain"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -p)
        [[ $# -ge 2 ]] || deny "missing protocol"
        proto="$2"; require_proto "$proto"; shift 2
        ;;
      --dport)
        [[ $# -ge 2 ]] || deny "missing port"
        port="$2"; require_port "$port"; shift 2
        ;;
      -s)
        [[ $# -ge 2 ]] || deny "missing source network"
        network="$2"; require_ip_or_cidr "$network"; shift 2
        ;;
      -j)
        [[ $# -ge 2 ]] || deny "missing target"
        target="$2"
        [[ "$target" == "ACCEPT" || "$target" == "DROP" ]] || deny "unsupported target: $target"
        shift 2
        ;;
      *)
        deny "unsupported ${binary} argument: $1"
        ;;
    esac
  done
  [[ -n "$target" ]] || deny "missing target"
  if [[ -n "$port" && -z "$proto" ]]; then
    deny "port rule requires protocol"
  fi
  if [[ "$binary" == "ip6tables" && -n "$network" && "$network" != *:* ]]; then
    return 0
  fi
  if [[ "$binary" == "iptables" && -n "$network" && "$network" == *:* ]]; then
    return 0
  fi
  iptables_ensure_opanel_chains
  argv=("$binary" "$op" "$chain")
  [[ -n "$proto" ]] && argv+=("-p" "$proto")
  [[ -n "$port" ]] && argv+=("--dport" "$port")
  [[ -n "$network" ]] && argv+=("-s" "$network")
  argv+=("-j" "$target")
  "${argv[@]}"
  # Without this the rule lives only in the running kernel: the on-disk snapshot
  # is taken elsewhere and never refreshed, so every admin rule vanished on the
  # next reboot while the panel kept listing it.
  firewall_persist_rules 2>/dev/null || true
}

run_managed_ipset_command() {
  [[ $# -ge 1 ]] || deny "usage: ipset-run <create|flush|add|destroy|list> ..."
  case "$1" in
    create)
      [[ $# -ge 3 ]] || deny "usage: ipset-run create <set> <type> ..."
      [[ "$2" == "$BLOCKLIST_IPSET_V4" || "$2" == "$BLOCKLIST_IPSET_V6" ]] || deny "unsupported ipset: $2"
      exec ipset "$@"
      ;;
    flush|destroy|list)
      [[ $# -eq 2 ]] || deny "usage: ipset-run $1 <set>"
      [[ "$2" == "$BLOCKLIST_IPSET_V4" || "$2" == "$BLOCKLIST_IPSET_V6" ]] || deny "unsupported ipset: $2"
      exec ipset "$@"
      ;;
    add)
      [[ $# -ge 3 ]] || deny "usage: ipset-run add <set> <network> ..."
      [[ "$2" == "$BLOCKLIST_IPSET_V4" || "$2" == "$BLOCKLIST_IPSET_V6" ]] || deny "unsupported ipset: $2"
      require_ip_or_cidr "$3"
      exec ipset "$@"
      ;;
    *)
      deny "unsupported ipset operation: $1"
      ;;
  esac
}

require_time_hhmm() {
  local value="$1" hour minute
  [[ "$value" =~ ^[0-9]{2}:[0-9]{2}$ ]] || deny "invalid time: $value"
  hour="${value%%:*}"; minute="${value##*:}"
  (( 10#$hour >= 0 && 10#$hour <= 23 )) || deny "invalid hour: $hour"
  (( 10#$minute >= 0 && 10#$minute <= 59 )) || deny "invalid minute: $minute"
}

schedule_panel_restart() {
  local unit
  systemctl daemon-reload || true
  if command -v systemd-run >/dev/null 2>&1; then
    unit="opanel-api-delayed-restart-$(date +%s)"
    systemd-run --unit="$unit" --on-active=2s /bin/systemctl restart opanel-api >/dev/null 2>&1 || true
  else
    (sleep 2; systemctl restart opanel-api >/dev/null 2>&1 || true) >/dev/null 2>&1 &
  fi
}

refresh_tools_ols() {
  local port domain host api_scheme tools_scheme pma_secure php_version panel_cert panel_key default_ver_no_dot lsphp_sock
  port="$(env_get PANEL_PORT)"; port="${port:-$DEFAULT_PANEL_PORT}"
  domain="$(env_get PANEL_DOMAIN)"; host="${domain:-$(detect_ip)}"
  panel_cert="$(env_get PANEL_SSL_CERT)"; panel_key="$(env_get PANEL_SSL_KEY)"
  php_version="${PHP_DEFAULT:-8.4}"
  default_ver_no_dot="${php_version//./}"
  lsphp_sock="/tmp/lshttpd/lsphp${default_ver_no_dot}.sock"
  api_scheme="http"; tools_scheme="http"; pma_secure="false"
  if panel_tls_enabled; then
    api_scheme="https"; tools_scheme="https"; pma_secure="true"
  fi
  ensure_ols_conf_dir_writable
  # The IP blocklist is not part of the web configuration and has its own
  # opanel-firewall-blocklist.timer plus a blocklist-apply command. Re-applying
  # it from here made every caller -- issuing SSL, refreshing a vhost -- pay for
  # a full reload of the set, which is minutes once the list reaches six figures.
  cat >"${OLS_VHOSTS_DIR}/00-opanel-tools.conf" <<OLS_VHOST
docRoot                   /usr/share/phpmyadmin/
vhDomain                  ${host}
enableIpGeo               0
allowSymbolLink           1

context /.well-known/acme-challenge/ {
  type                    static
  location                /var/www/opanel-acme/.well-known/acme-challenge/
  allowBrowse             1
  addDefaultCharset       off
}

context / {
  type                    null
  location                /usr/share/phpmyadmin/
  allowBrowse             1
}

extprocessor lsphp${default_ver_no_dot} {
  type                    lsapi
  address                 uds://${lsphp_sock}
  maxConns                10
  env                     PHP_LSAPI_CHILDREN=10
  initTimeout             60
  retryTimeout            0
  persistConn             1
  pcKeepAliveTimeout      1
  respBuffer              0
  autoStart               1
  path                    /usr/local/lsws/lsphp${default_ver_no_dot}/bin/lsphp
  backlog                 100
  instances               1
  extUser                 www-data
  extGroup                www-data
  # phpMyAdmin's PHP starts with the first request and stops after 5 idle
  # minutes: resident from boot it held ~39 MB on a server nobody had opened
  # phpMyAdmin on (measured on a fresh 2 GB VPS, 2026-09-30).
  runOnStartUp            0
  extMaxIdleTime          300
}

scripthandler {
  add                     lsapi:lsphp${default_ver_no_dot} php
}

rewrite  {
  enable                  1
  rules                   rewriteRule ^/phpmyadmin/(.*)$ /\$1 [L]
}

vhssl  {
  keyFile                 ${panel_key:-/dev/null}
  certFile                ${panel_cert:-/dev/null}
}

phpIniOverride  {
  php_value               include_path .:/usr/share/php
  php_value               upload_max_filesize 1024M
  php_value               post_max_size 1024M
  php_value               memory_limit 512M
  php_value               max_execution_time 300
  php_value               max_input_time 600
}
OLS_VHOST
  # Replace phpMyAdmin symlinks pointing outside docRoot with actual files
  # so OLS can serve static assets (CSS, JS) without symlink restrictions
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "
import pathlib, shutil
phpmyadmin = pathlib.Path('/usr/share/phpmyadmin')
for link in phpmyadmin.rglob('*'):
    if link.is_symlink():
        target = link.resolve()
        if target.exists() and not str(target).startswith(str(phpmyadmin)):
            link.unlink()
            if target.is_dir():
                shutil.copytree(str(target), str(link), symlinks=False)
            else:
                shutil.copy2(str(target), str(link))
" 2>/dev/null || true
  fi
  sed -i -E "/api\/databases\/phpmyadmin-sso/s#'[^']+/api/databases/phpmyadmin-sso/'#'${api_scheme}://127.0.0.1:${port}/api/databases/phpmyadmin-sso/'#" /usr/share/phpmyadmin/opanel-signon.php 2>/dev/null || true
  sed -i -E "s#('secure' => )(true|false)#\1${pma_secure}#" /etc/phpmyadmin/conf.d/opanel-signon.php /usr/share/phpmyadmin/opanel-signon.php 2>/dev/null || true
  # Migrate the sign-on session store off the shared parent. install.sh writes
  # the new path for fresh installs, but an existing box only ever gets a new
  # helper -- and ensure_php_runtime_dirs now tightens /var/lib/php/sessions to
  # 0751 root:root, which would leave phpMyAdmin unable to write its sessions
  # there. Doing it here means the fix arrives with the helper instead of one
  # release late.
  sed -i -E "s#/var/lib/php/sessions'#/var/lib/php/pma-sessions'#g" \
    /etc/phpmyadmin/conf.d/opanel-signon.php /usr/share/phpmyadmin/opanel-signon.php 2>/dev/null || true
  [[ -n "$host" ]] && sed -i -E "/PmaAbsoluteUri/s#'https?://[^']+/phpmyadmin/'#'${tools_scheme}://${host}/phpmyadmin/'#" /etc/phpmyadmin/conf.d/opanel-signon.php 2>/dev/null || true
  local pma_signon_secret
  pma_signon_secret="$(env_get PMA_SIGNON_SECRET)"
  [[ -n "$pma_signon_secret" ]] && sed -i -E "s#(X-OPanel-Signon-Secret: )[^']*#\1${pma_signon_secret}#" /usr/share/phpmyadmin/opanel-signon.php 2>/dev/null || true
  # This file holds the signon secret, so it is group-readable and no wider.
  # The previous comment here claimed 0640 could not work because the group is
  # opanel-sites rather than www-data -- but www-data is added to opanel-sites
  # by install.sh, ensure_sites_group and update.sh alike, so 0640 is exactly
  # what it needs. fix_phpmyadmin_permissions below sets group and mode for
  # this file too; the chmod is kept only to close the window before it runs.
  chmod 0640 /etc/phpmyadmin/conf.d/opanel-signon.php 2>/dev/null || true
  fix_phpmyadmin_permissions
  ols_sync_main_config
  restart_openlitespeed 2>/dev/null || true
}

configure_unattended_upgrades() {
  local enabled="$1" mode="$2" reboot="$3" origins
  [[ "$enabled" == "on" || "$enabled" == "off" ]] || deny "enabled must be on/off"
  [[ "$mode" == "security" || "$mode" == "all" ]] || deny "mode must be security/all"
  [[ "$reboot" == "on" || "$reboot" == "off" ]] || deny "auto reboot must be on/off"

  DEBIAN_FRONTEND=noninteractive apt-get update --allow-releaseinfo-change
  DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades apt-listchanges

  if [[ "$enabled" == "off" ]]; then
    cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Unattended-Upgrade "0";
APT
    systemctl disable --now unattended-upgrades.service 2>/dev/null || true
    echo "OS auto updates disabled"
    return 0
  fi

  origins='        "${distro_id}:${distro_codename}-security";'
  if [[ "$mode" == "all" ]]; then
    origins='        "${distro_id}:${distro_codename}";
        "${distro_id}:${distro_codename}-updates";
        "${distro_id}:${distro_codename}-security";'
  fi

  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APT
  cat >/etc/apt/apt.conf.d/51opanel-unattended-upgrades <<APT
Unattended-Upgrade::Allowed-Origins {
${origins}
};
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "$([[ "$reboot" == "on" ]] && echo true || echo false)";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
APT
  systemctl enable --now unattended-upgrades.service 2>/dev/null || true
  echo "OS auto updates enabled (${mode}, reboot=${reboot})"
}

run_os_update_now() {
  export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
  apt-get update --allow-releaseinfo-change
  apt-get \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    upgrade -y
}

run_os_update() {
  local unit="opanel-os-update"
  if systemctl is-active --quiet "${unit}.service"; then
    echo "OS update is already running: ${unit}.service"
    return 0
  fi
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run \
      --unit="$unit" \
      --collect \
      --description="Update OS packages for opanel" \
      /bin/bash -lc 'export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none; apt-get update --allow-releaseinfo-change; apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y'
    echo "OS update started: ${unit}.service"
    echo "Check progress: journalctl -u ${unit}.service -f"
    return 0
  fi
  nohup /bin/bash -lc 'export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none; apt-get update --allow-releaseinfo-change; apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y' \
    >/var/log/opanel-os-update.log 2>&1 &
  echo "OS update started in background. Log: /var/log/opanel-os-update.log"
}

write_panel_auto_update_timer() {
  local enabled="$1" time_value="$2"
  [[ "$enabled" == "on" || "$enabled" == "off" ]] || deny "enabled must be on/off"
  require_time_hhmm "$time_value"
  if [[ "$enabled" == "off" ]]; then
    systemctl disable --now opanel-auto-update.timer 2>/dev/null || true
    rm -f /etc/systemd/system/opanel-auto-update.service /etc/systemd/system/opanel-auto-update.timer
    systemctl daemon-reload
    echo "Panel auto update disabled"
    return 0
  fi
  [[ -f "$UPDATE_SCRIPT" ]] || deny "missing $UPDATE_SCRIPT"
  cat >/etc/systemd/system/opanel-auto-update.service <<SERVICE
[Unit]
Description=Update opanel from GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=SOURCE_DIR=${SOURCE_DIR}
Environment=APP_DIR=${APP_DIR}
Environment=REPO_URL=${REPO_URL:-https://github.com/bnixvn/opanel.git}
Environment=GIT_REMOTE=${GIT_REMOTE:-origin}
Environment=UPDATE_CHANNEL=${UPDATE_CHANNEL:-branch}
Environment=BRANCH=$(panel_update_branch)
Environment=RELEASE_TAG=${RELEASE_TAG:-}
Environment=RELEASE_PATTERN=${RELEASE_PATTERN:-v[0-9]*.[0-9]*.[0-9]*}
Environment=SKIP_PULL=${SKIP_PULL:-false}
ExecStart=/bin/bash ${UPDATE_SCRIPT}
SERVICE
  cat >/etc/systemd/system/opanel-auto-update.timer <<TIMER
[Unit]
Description=Run opanel auto update daily

[Timer]
OnCalendar=*-*-* ${time_value}:00
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
TIMER
  systemctl daemon-reload
  systemctl enable --now opanel-auto-update.timer
  echo "Panel auto update enabled at ${time_value}"
}

# The branch this box updates from: opanel_UPDATE_BRANCH in backend/.env (the
# staging box sets "staging"), else main. sudo resets the environment, so a
# BRANCH the panel exported never arrives here; the .env is the one place all
# three update paths -- this button, the auto-update timer and a bare
# opanel-update -- can agree on.
panel_update_branch() {
  local branch
  branch="$(env_get opanel_UPDATE_BRANCH)"
  branch="${branch//\"/}"
  branch="${branch:-main}"
  git check-ref-format --branch "$branch" >/dev/null 2>&1 || branch="main"
  printf '%s' "$branch"
}

run_panel_update() {
  [[ -f "$UPDATE_SCRIPT" ]] || deny "missing $UPDATE_SCRIPT"
  local unit="opanel-panel-update"
  if systemctl is-active --quiet "${unit}.service"; then
    echo "Panel update is already running: ${unit}.service"
    return 0
  fi
  if command -v systemd-run >/dev/null 2>&1; then
    systemd-run \
      --unit="$unit" \
      --collect \
      --description="Update opanel from GitHub" \
      --property="Environment=SOURCE_DIR=${SOURCE_DIR}" \
      --property="Environment=APP_DIR=${APP_DIR}" \
      --property="Environment=REPO_URL=${REPO_URL:-https://github.com/bnixvn/opanel.git}" \
      --property="Environment=GIT_REMOTE=${GIT_REMOTE:-origin}" \
      --property="Environment=UPDATE_CHANNEL=${UPDATE_CHANNEL:-branch}" \
      --property="Environment=BRANCH=$(panel_update_branch)" \
      --property="Environment=RELEASE_TAG=${RELEASE_TAG:-}" \
      --property="Environment=RELEASE_PATTERN=${RELEASE_PATTERN:-v[0-9]*.[0-9]*.[0-9]*}" \
      --property="Environment=SKIP_PULL=${SKIP_PULL:-false}" \
      /bin/bash "$UPDATE_SCRIPT"
    echo "Panel update started: ${unit}.service"
    echo "Check progress: journalctl -u ${unit}.service -f"
    return 0
  fi
  nohup env \
    SOURCE_DIR="$SOURCE_DIR" \
    APP_DIR="$APP_DIR" \
    REPO_URL="${REPO_URL:-https://github.com/bnixvn/opanel.git}" \
    GIT_REMOTE="${GIT_REMOTE:-origin}" \
    UPDATE_CHANNEL="${UPDATE_CHANNEL:-branch}" \
    BRANCH="$(panel_update_branch)" \
    RELEASE_TAG="${RELEASE_TAG:-}" \
    RELEASE_PATTERN="${RELEASE_PATTERN:-v[0-9]*.[0-9]*.[0-9]*}" \
    SKIP_PULL="${SKIP_PULL:-false}" \
    /bin/bash "$UPDATE_SCRIPT" \
    >/var/log/opanel-panel-update.log 2>&1 &
  echo "Panel update started in background. Log: /var/log/opanel-panel-update.log"
}

write_modsec_base_conf() {
  install -d -o root -g root -m 0755 /usr/local/lsws/conf/opanel/waf /usr/local/lsws/conf/opanel/waf/sites
  {
    [[ -f /etc/modsecurity/modsecurity.conf ]] && echo "Include /etc/modsecurity/modsecurity.conf"
    echo "SecRuleEngine On"
    # libmodsecurity3 skips the whole of phase 2 when body access is off, so
    # `SecRequestBodyAccess Off` silently disabled every phase:2 rule -- not
    # just the body ones. The wp2shell block, the ?rest_route smuggling block
    # and author enumeration are all phase:2 and never fired on any site.
    #
    # The limits keep large media/plugin uploads working, and must come after
    # the distro modsecurity.conf include above, which ships a 13 MB limit with
    # SecRequestBodyLimitAction Reject: only the first 1 MB of non-file content
    # is buffered, and a body over the total limit is inspected as far as it
    # goes instead of being rejected outright.
    echo "SecRequestBodyAccess On"
    echo "SecRequestBodyLimit 134217728"
    echo "SecRequestBodyNoFilesLimit 1048576"
    echo "SecRequestBodyLimitAction ProcessPartial"
  } >/usr/local/lsws/conf/opanel/waf/opanel-base.conf
}

write_modsec_main_conf() {
  write_waf_default_rules
  write_modsec_base_conf
  touch /usr/local/lsws/conf/opanel/waf/opanel-custom.conf
  {
    echo "Include /usr/local/lsws/conf/opanel/waf/opanel-base.conf"
    echo "Include /usr/local/lsws/conf/opanel/waf/opanel-default.conf"
    echo "Include /usr/local/lsws/conf/opanel/waf/opanel-custom.conf"
  } >/usr/local/lsws/conf/opanel/waf/opanel-main.conf
}

ensure_firewall_rule_store() {
  install -d -o opanel -g opanel -m 0750 "$opanel_DATA_DIR/firewall"
  if [[ -f "$opanel_DATA_DIR/firewall/rules.json" ]]; then
    chown opanel:opanel "$opanel_DATA_DIR/firewall/rules.json" 2>/dev/null || true
    chmod 0640 "$opanel_DATA_DIR/firewall/rules.json" 2>/dev/null || true
  fi
}

write_waf_default_rules() {
  install -d -o root -g root -m 0755 /usr/local/lsws/conf/opanel/waf
  cat >/usr/local/lsws/conf/opanel/waf/opanel-default.conf <<'RULES'
# OPanel default WAF rules: lightweight WordPress, Laravel, and PHP probes only.
SecRule REQUEST_URI "@rx (?i)(?:/\.env(?:\.|$)|/\.user\.ini(?:\.|$)|/\.git/|/composer\.(?:json|lock)(?:$|[?])|/(?:phpinfo|info)\.php(?:$|[?])|/(?:config|database|db)\.php\.(?:bak|old|save|txt)(?:$|[?]))" "id:1001301,phase:1,deny,status:403,log,msg:'opanel blocked PHP sensitive file probe'"
SecRule REQUEST_URI|ARGS "@rx (?i)(?:\.\./|\.\.\\|%2e%2e%2f|%252e%252e%252f)" "id:1001302,phase:2,deny,status:403,log,msg:'opanel blocked PHP path traversal'"
SecRule REQUEST_URI "@rx (?i)(?:/(?:c99|r57|shell|cmd|wso)\.php(?:$|[?])|/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin\.php(?:$|[?]))" "id:1001303,phase:1,deny,status:403,log,msg:'opanel blocked PHP runtime probe'"
SecRule REQUEST_URI "@rx (?i)(?:/\.env(?:\.|$)|/artisan(?:$|[?])|/server\.php(?:$|[?])|/storage/logs/[^?]*\.log(?:$|[?])|/bootstrap/cache/[^?]*\.php(?:$|[?]))" "id:1001201,phase:1,deny,status:403,log,msg:'opanel blocked Laravel sensitive path'"
SecRule REQUEST_URI "@rx (?i)(?:/_ignition/execute-solution(?:$|[?]))" "id:1001202,phase:1,deny,status:403,log,msg:'opanel blocked Laravel Ignition RCE probe'"
SecRule REQUEST_URI "@rx (?i)(?:/wp-config\.php(?:\.|$|[?])|/wp-content/(?:uploads|cache|upgrade)/[^?]*\.php(?:$|[?])|/wp-admin/includes/[^?]*\.php(?:$|[?])|/wp-includes/[^?]*\.php(?:$|[?]))" "id:1001101,phase:1,deny,status:403,log,msg:'opanel blocked WordPress sensitive path'"
SecRule REQUEST_URI "@rx (?i)(?:/xmlrpc\.php(?:$|[?]))" "id:1001102,phase:1,deny,status:403,log,msg:'opanel blocked WordPress XML-RPC access'"
SecRule ARGS:author "@rx ^[0-9]+$" "id:1001103,phase:2,deny,status:403,log,msg:'opanel blocked WordPress author enumeration'"
SecRule REQUEST_URI "@rx (?i)(?:/wp-admin/install\.php(?:$|[?])|/wp-admin/setup-config\.php(?:$|[?]))" "id:1001104,phase:1,deny,status:403,log,msg:'opanel blocked WordPress installer probe'"
RULES
}

save_waf_custom_rules() {
  install -d -o root -g root -m 0755 /usr/local/lsws/conf/opanel/waf
  write_waf_default_rules
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp"
  if file_has_nul "$tmp"; then
    rm -f "$tmp"
    deny "WAF rules cannot contain NUL bytes"
  fi
  if [[ $(wc -c <"$tmp") -gt 65536 ]]; then
    rm -f "$tmp"
    deny "WAF custom rules must be 64 KB or smaller"
  fi
  install -m 0644 -o root -g root "$tmp" /usr/local/lsws/conf/opanel/waf/opanel-custom.conf
  rm -f "$tmp"
  write_modsec_main_conf
  restart_openlitespeed
  echo "WAF custom rules saved"
}

save_waf_site_rules() {
  local domain="$1" defer="${2:-}" tmp target backup=""
  require_domain "$domain"
  [[ "$defer" == "" || "$defer" == "defer" ]] || deny "invalid waf-site-save mode: $defer"
  install -d -o root -g root -m 0755 /usr/local/lsws/conf/opanel/waf /usr/local/lsws/conf/opanel/waf/sites
  write_modsec_base_conf
  tmp="$(mktemp)"
  cat >"$tmp"
  if file_has_nul "$tmp"; then
    rm -f "$tmp"
    deny "WAF rules cannot contain NUL bytes"
  fi
  if [[ $(wc -c <"$tmp") -gt 163840 ]]; then
    rm -f "$tmp"
    deny "WAF site rules must be 160 KB or smaller"
  fi
  target="/usr/local/lsws/conf/opanel/waf/sites/${domain}.conf"
  # Nothing to do (and no reason to restart OLS) when the rendered rules are
  # byte-identical to what is already on disk -- the common case on a bulk
  # refresh where the rule set has not changed.
  if [[ -f "$target" ]] && cmp -s "$tmp" "$target"; then
    rm -f "$tmp"
    echo "WAF site rules unchanged: ${domain}"
    return 0
  fi
  if [[ -f "$target" ]]; then
    backup="${target}.bak.$(date +%s)"
    cp "$target" "$backup"
  fi
  install -m 0644 -o root -g root "$tmp" "$target"
  rm -f "$tmp"
  # "defer" skips the reload so a bulk caller can restart OLS once at the end.
  [[ "$defer" == "defer" ]] || restart_openlitespeed
  rm -f "$backup" 2>/dev/null || true
  echo "WAF site rules saved: ${domain}"
}

install_waf_engine() {
  export DEBIAN_FRONTEND=noninteractive
  if ! dpkg -s ols-modsecurity >/dev/null 2>&1; then
    apt-get update --allow-releaseinfo-change
    apt-get install -y ols-modsecurity modsecurity-crs libmodsecurity3 2>/dev/null || \
      apt-get install -y ols-modsecurity libmodsecurity3 2>/dev/null || \
      deny "Could not install ols-modsecurity"
  elif ! dpkg -s modsecurity-crs >/dev/null 2>&1; then
    apt-get update --allow-releaseinfo-change
    apt-get install -y modsecurity-crs libmodsecurity3 2>/dev/null || true
  fi
  [[ -f /usr/local/lsws/modules/mod_security.so ]] || deny "OpenLiteSpeed mod_security.so is missing"
  install -d -o root -g root -m 0755 /usr/local/lsws/conf/opanel/waf /usr/local/lsws/conf/opanel/waf/sites
  write_waf_default_rules
  touch /usr/local/lsws/conf/opanel/waf/opanel-custom.conf
  if [[ -f /etc/modsecurity/modsecurity.conf-recommended && ! -f /etc/modsecurity/modsecurity.conf ]]; then
    cp /etc/modsecurity/modsecurity.conf-recommended /etc/modsecurity/modsecurity.conf
  fi
  if [[ -f /etc/modsecurity/modsecurity.conf ]]; then
    sed -i -E 's/^SecRuleEngine .*/SecRuleEngine On/' /etc/modsecurity/modsecurity.conf
  fi
  write_modsec_main_conf
  ensure_ols_modsecurity_enabled
  ols_sync_main_config
  restart_openlitespeed
  echo "WAF engine installed with opanel lightweight WordPress/Laravel/PHP rules."
}

install_clamav_engine() {
  export DEBIAN_FRONTEND=noninteractive
  # Wait for another apt run (an addon install, unattended-upgrades) rather
  # than failing on its lock.
  apt-get -o DPkg::Lock::Timeout=300 update --allow-releaseinfo-change
  if ! dpkg -s clamav clamav-daemon >/dev/null 2>&1; then
    apt-get -o DPkg::Lock::Timeout=300 install -y clamav clamav-daemon
  fi
  # Ensure the daemon socket directory exists and the service is enabled.
  install -d -o clamav -g clamav -m 0755 /run/clamav 2>/dev/null || true
  systemctl enable --now clamav-daemon
  # Triggers an initial signature database refresh in the background.
  freshclam >/dev/null 2>&1 || true
  echo "ClamAV installed and clamav-daemon enabled."
  # Layer Linux Malware Detect on top -- its web-focused signature set catches
  # the PHP shells / injections ClamAV's general signatures miss, and it uses the
  # resident clamd as its scan engine (both signature sets, one fast scanner).
  install_lmd_engine || echo "NOTE: Linux Malware Detect not installed; ClamAV scanning still works."
}

# github.com/rfxn/linux-malware-detect. Pinned tag; update deliberately.
LMD_GIT_TAG="v2.0.1"
LMD_DIR="/usr/local/maldetect"
LMD_CONF="${LMD_DIR}/conf.maldet"

lmd_installed() { command -v maldet >/dev/null 2>&1 && [[ -f "$LMD_CONF" ]]; }

install_lmd_engine() {
  export DEBIAN_FRONTEND=noninteractive
  command -v clamdscan >/dev/null 2>&1 || { echo "LMD needs ClamAV first"; return 1; }
  if ! lmd_installed; then
    apt-get install -y git ca-certificates inotify-tools >/dev/null 2>&1 || true
    local tmp
    tmp="$(mktemp -d)" || return 1
    if ! git clone --depth 1 --branch "$LMD_GIT_TAG" \
        https://github.com/rfxn/linux-malware-detect.git "$tmp/lmd" >/dev/null 2>&1; then
      rm -rf -- "$tmp"; echo "failed to clone linux-malware-detect@${LMD_GIT_TAG}"; return 1
    fi
    ( cd "$tmp/lmd" && ./install.sh ) >/dev/null 2>&1 || { rm -rf -- "$tmp"; return 1; }
    rm -rf -- "$tmp"
  fi
  lmd_installed || { echo "maldet not on PATH after install"; return 1; }
  configure_lmd
  # LMD ships its own daily cron; opanel drives the schedule instead.
  rm -f /etc/cron.d/maldet /etc/cron.daily/maldet 2>/dev/null || true
  # ...and its own monitor unit; opanel's real-time toggle is the only one.
  mask_stock_lmd_unit
  lmd_ignore_inotify_add_missing || true
  ( maldet -u >/dev/null 2>&1; maldet -d >/dev/null 2>&1 ) &
  echo "Linux Malware Detect ${LMD_GIT_TAG} installed (engine: clamd)."
}

# Opanel's opinionated conf.maldet: use clamd, never auto-quarantine or suspend a
# user (a false positive on a legit plugin would take a live site down -- hits
# are surfaced in the panel and the admin decides), keep signatures current.
set_lmd_conf() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" "$LMD_CONF"; then
    sed -i "s|^${k}=.*|${k}=\"${v}\"|" "$LMD_CONF"
  else
    printf '%s="%s"\n' "$k" "$v" >>"$LMD_CONF"
  fi
}

configure_lmd() {
  [[ -f "$LMD_CONF" ]] || return 0
  set_lmd_conf scan_clamscan 1
  set_lmd_conf clamav_scan 1
  set_lmd_conf autoupdate_signatures 1
  set_lmd_conf autoupdate_version 1
  set_lmd_conf cron_daily_scan 0
  set_lmd_conf quarantine_hits 0
  set_lmd_conf quarantine_clean 0
  set_lmd_conf quarantine_suspend_user 0
  set_lmd_conf email_alert 0
  set_lmd_conf scan_ignore_root 1
  set_lmd_conf scan_max_filesize 2048k
  set_lmd_conf scan_tmpdir /var/tmp
}

LMD_MONITOR_UNIT="/etc/systemd/system/opanel-maldet-monitor.service"
LMD_MONITOR_PID="${LMD_DIR}/tmp/monitor.pid"
LMD_IGNORE_INOTIFY="${LMD_DIR}/ignore_inotify"
# Never a threat, and together ~80% of the monitor's events on a live box:
# OpenLiteSpeed rewrites its real-time report under /dev/shm every second, and
# clamd's HTML normaliser leaves a temp dir in /tmp for every page it scans,
# which the monitor queued and then failed on ("Can't access file") because it
# was already gone. Raw ERE, matched by inotifywait against the full path.
LMD_IGNORE_INOTIFY_ENTRIES=(
  '^/dev/shm/ols/status/'
  '^/tmp/html-tmp\.[0-9a-f]+(/|$)'
)

# LMD's installer ships and enables its own maldet.service (--monitor users).
# Beside opanel's unit that is a second supervisor on the same inotify log,
# read cursor and monitor.pid: the two split one event queue, both got past
# maldet's one-monitor check by starting in the same second at boot, and
# `maldet -k` stops that unit instead of opanel's. Its "users" mode looks for
# ~/public_html, which opanel's layout never has, so all it ever watched were
# the temp dirs -- now in opanel's own unit. Masked, not just disabled: LMD's
# self-update re-runs install.sh, which re-enables the unit and restarts it.
mask_stock_lmd_unit() {
  [[ -f /usr/lib/systemd/system/maldet.service || -f /lib/systemd/system/maldet.service ]] || return 0
  [[ "$(systemctl is-enabled maldet.service 2>/dev/null || true)" == "masked" ]] && return 0
  systemctl disable --now maldet.service >/dev/null 2>&1 || true
  systemctl mask maldet.service >/dev/null 2>&1 || true
}

# Returns 0 when an entry was added (a running monitor needs a restart to pick
# it up: the exclude list is built once, when inotifywait starts).
lmd_ignore_inotify_add_missing() {
  local entry added=1
  [[ -d "$LMD_DIR" ]] || return 1
  touch "$LMD_IGNORE_INOTIFY"
  # LMD rewrites this file itself; make sure an entry never lands on the end
  # of a last line that has no newline.
  if [[ -s "$LMD_IGNORE_INOTIFY" && -n "$(tail -c1 "$LMD_IGNORE_INOTIFY")" ]]; then
    echo >>"$LMD_IGNORE_INOTIFY"
  fi
  for entry in "${LMD_IGNORE_INOTIFY_ENTRIES[@]}"; do
    grep -qxF -- "$entry" "$LMD_IGNORE_INOTIFY" && continue
    printf '%s\n' "$entry" >>"$LMD_IGNORE_INOTIFY"
    added=0
  done
  return "$added"
}

# Stops a monitor that runs outside opanel's unit (a hand-started one, or the
# stock unit's), and drops a monitor.pid that names no monitor. maldet refuses
# to start while that file names a live process, and the file outlives the
# process: after a reboot it can name whatever unrelated process got the old
# pid, and every start would then exit 1 -- or `maldet -k` would kill it.
lmd_stop_stray_monitor() {
  local pid=""
  if systemctl is-active --quiet maldet.service 2>/dev/null; then
    systemctl stop maldet.service >/dev/null 2>&1 || true
  fi
  [[ -f "$LMD_MONITOR_PID" ]] || return 0
  pid="$(tr -dc '0-9' <"$LMD_MONITOR_PID" 2>/dev/null || true)"
  if [[ -n "$pid" && -r "/proc/${pid}/cmdline" ]] \
      && tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null | grep -q 'maldet --monitor'; then
    maldet -k >/dev/null 2>&1 || true
  fi
  rm -f "$LMD_MONITOR_PID"
}

# Returns 0 when the unit file changed.
write_lmd_monitor_unit() {
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp" <<'UNIT'
[Unit]
Description=OPanel Linux Malware Detect real-time monitor
After=clamav-daemon.service
Wants=clamav-daemon.service

[Service]
# `maldet --monitor` stays in the foreground for as long as it watches, so it
# is a simple service, not a oneshot. As a oneshot systemd waited for it to
# exit: first killing it at the 120s timeout on a busy host and marking the
# unit failed, then -- with the timeout lifted -- sitting in "activating"
# forever, which is_active reports as not running. Either way the panel's
# real-time status was wrong while the watcher itself was fine.
Type=simple
# maldet refuses to start while its monitor.pid names a live process; clear a
# stray monitor or a stale pid file first (lmd_stop_stray_monitor). The leading
# - keeps a start from failing on it.
ExecStartPre=-/usr/bin/env SUDO_USER=opanel /usr/local/sbin/opanel-helper maldet-monitor prestart
# The sites, and the temp dirs that droppers and miners are written to.
ExecStart=/usr/local/sbin/maldet --monitor /home,/tmp,/var/tmp,/dev/shm
# No ExecStop: systemd sends the supervisor SIGTERM, and its handler stops
# inotifywait and cleans up. LMD 2.x has no "--monitor stop" -- it took "stop"
# for a path, logged "no valid option" and did nothing.
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
  if [[ -f "$LMD_MONITOR_UNIT" ]] && cmp -s "$tmp" "$LMD_MONITOR_UNIT"; then
    rm -f "$tmp"
    return 1
  fi
  install -m 0644 "$tmp" "$LMD_MONITOR_UNIT"
  rm -f "$tmp"
  return 0
}

enable_lmd_monitor() {
  lmd_installed || deny "Linux Malware Detect is not installed"
  # inotify watches for every file under /home -- a busy shared host has a lot.
  local wf=/etc/sysctl.d/60-opanel-inotify.conf
  printf 'fs.inotify.max_user_watches=1048576\nfs.inotify.max_user_instances=1024\n' >"$wf"
  sysctl -p "$wf" >/dev/null 2>&1 || true
  mask_stock_lmd_unit
  lmd_ignore_inotify_add_missing || true
  write_lmd_monitor_unit || true
  systemctl daemon-reload
  systemctl enable opanel-maldet-monitor.service >/dev/null 2>&1
  # restart, not enable --now: a running monitor keeps its old paths and
  # exclude list until it starts again.
  systemctl restart opanel-maldet-monitor.service
  echo "LMD real-time monitor enabled for /home, /tmp, /var/tmp and /dev/shm"
}

# The monitor unit is only written when someone turns real-time protection on,
# so this brings an existing box to the current layout on every update (called
# from log-hygiene). A box without the monitor still gets the stock unit masked,
# so a reboot cannot start a monitor the panel shows as off.
ensure_lmd_monitor_layout() {
  lmd_installed || return 0
  local restart=0
  mask_stock_lmd_unit
  if lmd_ignore_inotify_add_missing; then restart=1; fi
  [[ -f "$LMD_MONITOR_UNIT" ]] || return 0
  if write_lmd_monitor_unit; then
    systemctl daemon-reload
    restart=1
  fi
  if (( restart )) && systemctl is-enabled --quiet opanel-maldet-monitor.service 2>/dev/null; then
    systemctl restart opanel-maldet-monitor.service >/dev/null 2>&1 || true
  fi
  return 0
}

# The ClamAV packages the Malware Scanner addon installs, directly or as their
# dependencies. libclamav stays: a library another package may link against.
CLAMAV_PACKAGES=(clamav clamav-daemon clamav-freshclam clamav-base clamdscan)

# Removing the Malware Scanner addon: the real-time monitor, Linux Malware
# Detect and ClamAV go. The panel's own quarantine store and scan history under
# /var/lib/opanel are not touched -- a quarantined file is still somebody's.
remove_clamav_engine() {
  local installed=() pkg name ok
  export DEBIAN_FRONTEND=noninteractive
  disable_lmd_monitor >/dev/null 2>&1 || true
  if [[ -d "$LMD_DIR" ]] || command -v maldet >/dev/null 2>&1; then
    systemctl disable --now maldet.service >/dev/null 2>&1 || true
    systemctl unmask maldet.service >/dev/null 2>&1 || true
    rm -f /usr/lib/systemd/system/maldet.service /lib/systemd/system/maldet.service \
      /usr/local/sbin/maldet /usr/local/sbin/lmd /etc/cron.d/maldet /etc/cron.daily/maldet \
      /etc/default/maldet /etc/sysconfig/maldet
    rm -rf -- "${LMD_DIR:?}"
    systemctl daemon-reload
  fi
  systemctl disable --now clamav-daemon clamav-freshclam >/dev/null 2>&1 || true
  for pkg in "${CLAMAV_PACKAGES[@]}"; do
    dpkg-query -W -f '${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" && installed+=("$pkg")
  done
  if (( ${#installed[@]} )); then
    # Refuse when apt would take anything that is not ClamAV with it.
    for name in $(apt-get -s purge "${installed[@]}" 2>/dev/null | awk '/^(Purg|Remv) /{print $2}'); do
      ok=0
      for pkg in "${CLAMAV_PACKAGES[@]}"; do [[ "$name" == "$pkg" ]] && ok=1; done
      (( ok )) || deny "removing ClamAV would also remove $name"
    done
    apt-get -o DPkg::Lock::Timeout=120 purge -y "${installed[@]}" >/dev/null || deny "apt could not remove ClamAV"
  fi
  echo "Malware Scanner removed: ClamAV and Linux Malware Detect uninstalled"
}

disable_lmd_monitor() {
  systemctl disable --now opanel-maldet-monitor.service >/dev/null 2>&1 || true
  lmd_stop_stray_monitor
  rm -f "$LMD_MONITOR_UNIT"
  systemctl daemon-reload
  echo "LMD real-time monitor disabled"
}

update_malware_signatures() {
  freshclam >/dev/null 2>&1 || true
  if lmd_installed; then
    maldet -u >/dev/null 2>&1 || true
    maldet -d >/dev/null 2>&1 || true
  fi
  echo "malware signatures updated"
}

# --- Quarantine -------------------------------------------------------------
# opanel keeps its own per-file quarantine (LMD's quarantine_hits is left off so
# a false positive never takes a live site down silently). A quarantined file is
# moved to a root-only store with its origin, owner and mode recorded, so it can
# be restored byte-for-byte or dropped for good.
QUARANTINE_DIR="${opanel_DATA_DIR}/quarantine"

quarantine_dispatch() {
  install -d -o root -g root -m 0700 "$QUARANTINE_DIR" "$QUARANTINE_DIR/store"
  [[ -f "$QUARANTINE_DIR/index.json" ]] || { printf '[]' > "$QUARANTINE_DIR/index.json"; chmod 0600 "$QUARANTINE_DIR/index.json"; }
  python3 - "$QUARANTINE_DIR" "$@" <<'PY'
import hashlib, json, os, secrets, stat as stat_mod, sys, time

qdir = sys.argv[1]
store = os.path.join(qdir, "store")
index_path = os.path.join(qdir, "index.json")
action = sys.argv[2] if len(sys.argv) > 2 else "list"
args = sys.argv[3:]

# Malware lands in web content and world-writable spool dirs -- quarantine is
# limited to those. System paths are handled over SSH, not from the panel.
SAFE_PREFIXES = ("/home/", "/tmp/", "/var/tmp/", "/dev/shm/", "/var/www/")


def die(msg):
    sys.stderr.write("opanel-helper: " + msg + "\n")
    sys.exit(1)


def load():
    try:
        data = json.load(open(index_path, encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save(entries):
    tmp = index_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, index_path)


def check_path(p):
    if not p or not p.startswith("/") or "\x00" in p or "\n" in p:
        die("bad path")
    n = os.path.normpath(p)
    if ".." in n.split("/"):
        die("path traversal not allowed")
    if not any(n.startswith(pre) for pre in SAFE_PREFIXES):
        die("not a quarantine-eligible location: " + n)
    return n


def parent_fd(path):
    return os.open(os.path.dirname(path) or "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


if action == "list":
    entries = load()
    changed = False
    for e in list(entries):
        if not os.path.isfile(os.path.join(store, e["id"] + ".bin")):
            entries.remove(e)
            changed = True
    if changed:
        save(entries)
    json.dump(entries, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")

elif action == "add":
    if not args:
        die("usage: malware-quarantine add <path> [signature]")
    src = check_path(args[0])
    signature = (args[1] if len(args) > 1 else "")[:120]
    name = os.path.basename(src)
    if not name or name in (".", ".."):
        die("bad file name")
    pfd = parent_fd(src)
    try:
        st = os.stat(name, dir_fd=pfd, follow_symlinks=False)
        if not stat_mod.S_ISREG(st.st_mode):
            die("not a regular file: " + src)
        if st.st_size > 512 * 1024 * 1024:
            die("file too large to quarantine (>512MB): " + src)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
        try:
            chunks = []
            while True:
                buf = os.read(fd, 1 << 20)
                if not buf:
                    break
                chunks.append(buf)
            data = b"".join(chunks)
        finally:
            os.close(fd)
        qid = secrets.token_hex(16)
        blob = os.path.join(store, qid + ".bin")
        wfd = os.open(blob, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(wfd, "wb") as out:
            out.write(data)
        entry = {
            "id": qid,
            "original_path": src,
            "signature": signature,
            "size": st.st_size,
            "uid": st.st_uid,
            "gid": st.st_gid,
            "mode": stat_mod.S_IMODE(st.st_mode),
            "sha256": hashlib.sha256(data).hexdigest(),
            "quarantined_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # Record it before removing the original, so a crash never loses the file.
        entries = load()
        entries.insert(0, entry)
        save(entries)
        os.unlink(name, dir_fd=pfd)
    finally:
        os.close(pfd)
    json.dump(entry, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")

elif action == "restore":
    if not args:
        die("usage: malware-quarantine restore <id>")
    qid = args[0]
    if not (len(qid) == 32 and all(c in "0123456789abcdef" for c in qid)):
        die("bad id")
    entries = load()
    entry = next((e for e in entries if e["id"] == qid), None)
    if entry is None:
        die("no such quarantine entry: " + qid)
    dest = check_path(entry["original_path"])
    blob = os.path.join(store, qid + ".bin")
    if not os.path.isfile(blob):
        die("quarantined blob is missing")
    with open(blob, "rb") as fh:
        data = fh.read()
    pfd = parent_fd(dest)
    try:
        name = os.path.basename(dest)
        try:
            os.stat(name, dir_fd=pfd, follow_symlinks=False)
            die("a file already exists at the original path; not overwriting: " + dest)
        except FileNotFoundError:
            pass
        wfd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      int(entry.get("mode", 0o644)), dir_fd=pfd)
        try:
            mv = memoryview(data)
            while mv:
                mv = mv[os.write(wfd, mv):]
            os.fchown(wfd, int(entry.get("uid", 0)), int(entry.get("gid", 0)))
            os.fchmod(wfd, int(entry.get("mode", 0o644)))
        finally:
            os.close(wfd)
    finally:
        os.close(pfd)
    os.unlink(blob)
    save([e for e in entries if e["id"] != qid])
    sys.stdout.write("restored " + dest + "\n")

elif action == "drop":
    if not args:
        die("usage: malware-quarantine drop <id>")
    qid = args[0]
    if not (len(qid) == 32 and all(c in "0123456789abcdef" for c in qid)):
        die("bad id")
    entries = load()
    if not any(e["id"] == qid for e in entries):
        die("no such quarantine entry: " + qid)
    blob = os.path.join(store, qid + ".bin")
    if os.path.isfile(blob):
        os.unlink(blob)
    save([e for e in entries if e["id"] != qid])
    sys.stdout.write("deleted quarantine entry " + qid + "\n")

else:
    die("unknown quarantine action: " + action)
PY
}

# Directories a full-system scan must not walk into: kernel/device trees and
# live process state (not real files), the signature database (clamd's own
# working set), and the panel's backup archives, which are multi-GB tarballs of
# files the scan already covers in place.
CLAMAV_SCAN_PRUNE_PATHS=(/proc /sys /dev /run /var/lib/clamav /var/backups/opanel /snap)

# Incremental scans look back this many days of file changes; full scans run at
# least this often regardless of what the panel asks for.
LMD_INCREMENTAL_DAYS=7

pid_descends_from() {
  local pid="$1" ancestor="$2" ppid
  while [[ -n "$pid" && "$pid" -gt 1 ]]; do
    [[ "$pid" == "$ancestor" ]] && return 0
    ppid="$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)" || return 1
    pid="$ppid"
  done
  return 1
}

# LMD hands its whole file list to clamscan / clamdscan and then only prints a
# heartbeat ("N files | elapsed Xs"), so a scan of /home showed 0% for hours.
# The scanner reads that list front to back, one path per file it scans, so
# its read position in the list is the progress. Only scanners started by this
# helper run are looked at: the realtime monitor runs its own.
lmd_scan_progress_poller() {
  local owner="$1" pid fd target pos size last="" counted=""
  while sleep 5; do
    for pid in $(pgrep -x clamscan; pgrep -x clamdscan); do
      pid_descends_from "$pid" "$owner" || continue
      for fd in /proc/"$pid"/fd/*; do
        target="$(readlink "$fd" 2>/dev/null)" || continue
        case "$target" in /usr/local/maldetect/tmp/.find.*) ;; *) continue ;; esac
        size="$(stat -c %s "$target" 2>/dev/null)" || continue
        pos="$(sed -nE 's/^pos:[[:space:]]*([0-9]+).*/\1/p' "/proc/$pid/fdinfo/${fd##*/}" 2>/dev/null || true)"
        [[ -n "$pos" && "${size:-0}" -gt 0 ]] || continue
        # What LMD actually scans (an incremental run is a subset of the tree).
        if [[ "$counted" != "$target" ]]; then
          counted="$target"
          echo "opanel-scan-total $(wc -l <"$target" | tr -d '[:space:]')"
        fi
        [[ "$pos $size" == "$last" ]] && continue
        last="$pos $size"
        echo "opanel-scan-progress ${pos} ${size}"
      done
    done
  done
}

run_malware_lmd_scan() {
  local scan_root="$1" mode="${2:-full}" root tmp scanid report total hits total_pre poller
  systemctl is-active --quiet clamav-daemon 2>/dev/null || deny "clamav-daemon is not running"
  root="$scan_root"
  [[ "$root" == "/" ]] && root=/home
  [[ -d "$root" ]] || deny "scan path is not a directory: $root"

  # A quick pre-count gives the panel a progress denominator; maldet's own
  # streamed output keeps the pipe alive while it runs.
  total_pre="$( { find "$root" -type f 2>/dev/null || true; } | wc -l | tr -d '[:space:]')"
  echo "opanel-scan-total ${total_pre:-0}"
  echo "opanel-scan-mode ${mode}"

  tmp="$(mktemp /tmp/opanel-maldet.XXXXXX)" || deny "cannot create scan temp file"
  # A RETURN trap is not scoped to the function that sets it: it stays armed and
  # fires again when the *caller* returns, by which point this local is gone and
  # `set -u` kills the helper. run_clamav_system_scan calls this function, so
  # that fired on every LMD scan. Clear the trap as it runs, and read the path
  # defensively so a stray firing is a harmless no-op.
  trap 'rm -f "${tmp:-}"; trap - RETURN' RETURN
  lmd_scan_progress_poller "$$" &
  poller=$!
  if [[ "$mode" == "incremental" ]]; then
    { stdbuf -oL maldet -r "$root" "$LMD_INCREMENTAL_DAYS" 2>&1 || true; } | tee "$tmp" || true
  else
    { stdbuf -oL maldet -r "$root" 2>&1 || true; } | tee "$tmp" || true
  fi
  kill "$poller" 2>/dev/null || true
  wait "$poller" 2>/dev/null || true

  scanid="$(grep -oE '[0-9]{6}-[0-9]{4}\.[0-9]+' "$tmp" | tail -n1)"
  if [[ -z "$scanid" ]]; then
    echo "opanel-scan-scanned ${total_pre:-0}"
    return 0
  fi
  report="$(maldet --report "$scanid" 2>/dev/null)"
  total="$(printf '%s\n' "$report" | sed -nE 's/^TOTAL FILES:[[:space:]]*([0-9]+).*/\1/p' | head -n1)"
  hits="$(printf '%s\n' "$report" | sed -nE 's/^TOTAL HITS:[[:space:]]*([0-9]+).*/\1/p' | head -n1)"

  # FILE HIT LIST rows:  {HEX}php.base64.v23eb9 : /home/x/public_html/a.php
  printf '%s\n' "$report" | sed -nE 's|^(\{[^}]*\}[^:]*) : (/.+)$|\2: \1 FOUND|p'

  echo "opanel-scan-scanned ${total:-${total_pre:-0}}"
  echo "opanel-scan-report ${scanid}"
  [[ "${hits:-0}" -eq 0 ]] || return 1
  return 0
}

run_clamav_system_scan() {
  local scan_root="$1" mode="${2:-full}" list total prune=() path rc=0
  # LMD (LMD + ClamAV signatures, clamd as the engine, incremental support)
  # drives the website-tree scan. A whole-server scan also has to walk /usr,
  # /var, /etc and friends, which maldet -r is not built for, so "/" stays on
  # clamdscan with the prune list below.
  if lmd_installed && [[ "$scan_root" != "/" ]]; then
    run_malware_lmd_scan "$scan_root" "$mode" || rc=$?
    return "$rc"
  fi
  command -v clamdscan >/dev/null 2>&1 || deny "clamdscan is not installed"
  systemctl is-active --quiet clamav-daemon 2>/dev/null || deny "clamav-daemon is not running"

  for path in "${CLAMAV_SCAN_PRUNE_PATHS[@]}"; do
    prune+=(-path "$path" -o)
  done

  list="$(mktemp /tmp/opanel-clamav-list.XXXXXX)" || deny "cannot create scan list"
  trap 'rm -f "${list:-}"; trap - RETURN' RETURN
  find "$scan_root" \( "${prune[@]}" -false \) -prune -o -type f -print >"$list" 2>/dev/null || true
  total="$(wc -l <"$list" | tr -d "[:space:]")"
  # The panel reads this first line to size its progress bar; clamdscan itself
  # never reports a total.
  echo "opanel-scan-total ${total:-0}"
  [[ "${total:-0}" -gt 0 ]] || { echo "opanel-scan-empty"; return 0; }

  # stdbuf keeps the per-file lines flowing so progress updates while the scan
  # runs instead of arriving in one block at the end.
  stdbuf -oL clamdscan --fdpass --stdout --file-list="$list"
}

ensure_litespeed_php74_repo() {
  install -d -o root -g root -m 0755 /etc/apt/preferences.d /etc/apt/sources.list.d
  cat >/etc/apt/sources.list.d/opanel-lsphp74-jammy.list <<'EOF'
deb http://hk.archive.ubuntu.com/ubuntu jammy main universe multiverse restricted
deb http://hk.archive.ubuntu.com/ubuntu jammy-updates main universe multiverse restricted
deb http://security.ubuntu.com/ubuntu jammy-security main universe multiverse restricted
deb [trusted=yes] http://rpms.litespeedtech.com/debian/ jammy main
EOF
  cat >/etc/apt/preferences.d/opanel-lsphp74-jammy.pref <<'EOF'
Package: *
Pin: release n=jammy
Pin-Priority: 50

Package: lsphp74*
Pin: release n=jammy
Pin-Priority: 990

Package: libicu70 mime-support libmagickcore-6.q16-6 libmagickwand-6.q16-6 libtiff5
Pin: release n=jammy*
Pin-Priority: 990
EOF
  apt-get update --allow-releaseinfo-change
}

install_php_version() {
  local version="$1" lsphp_ver ini_dir
  export DEBIAN_FRONTEND=noninteractive
  require_php_version "$version"
  lsphp_ver="${version//./}"
  if [[ -x "/usr/local/lsws/lsphp${lsphp_ver}/bin/lsphp" ]]; then
    echo "LSPHP $version is already installed; ensuring opanel extension set..."
  fi
  if [[ "$version" == "7.4" ]]; then
    ensure_litespeed_php74_repo
  elif ! apt-cache show "lsphp${lsphp_ver}" >/dev/null 2>&1; then
    echo "Refreshing LiteSpeed package metadata for LSPHP $version..."
    curl -fsSL --connect-timeout 15 --max-time 120 https://repo.litespeed.sh | bash
    apt-get update --allow-releaseinfo-change
  fi
  echo "Installing LSPHP $version..."
  local packages=(
    "lsphp${lsphp_ver}"
    "lsphp${lsphp_ver}-common"
    "lsphp${lsphp_ver}-mysql"
    "lsphp${lsphp_ver}-sqlite3"
    "lsphp${lsphp_ver}-curl"
    "lsphp${lsphp_ver}-opcache"
    "lsphp${lsphp_ver}-intl"
    "lsphp${lsphp_ver}-redis"
    "lsphp${lsphp_ver}-imagick"
  )
  local available_packages=() missing_packages=() package
  for package in "${packages[@]}"; do
    if apt-cache show "$package" >/dev/null 2>&1; then
      available_packages+=("$package")
    else
      missing_packages+=("$package")
    fi
  done
  if [[ ${#missing_packages[@]} -gt 0 ]]; then
    echo "Skipping PHP packages not available in repo: ${missing_packages[*]}"
  fi
  [[ ${#available_packages[@]} -gt 0 ]] || deny "No LSPHP package found for PHP ${version}"
  apt-get install -y "${available_packages[@]}" || { echo "Failed to install LSPHP $version"; return 1; }
  ini_dir="/usr/local/lsws/lsphp${lsphp_ver}/etc/php/${version}/mods-available"
  install -d -o root -g root -m 0755 "$ini_dir"
  cat >"${ini_dir}/99-opanel.ini" <<INI
upload_max_filesize = 1024M
post_max_size = 1024M
memory_limit = 1024M
max_execution_time = 300
max_input_time = 600
max_input_vars = 10000
max_file_uploads = 100
; The ionCube loader installs a user opcode handler, so PHP turns JIT off by
; itself and prints a warning to stderr on every worker start. Turning it off
; here keeps the behaviour and loses the warning -- that warning wrote a 60 GB
; stderr.log on a live server. jit_buffer_size goes with it: nothing will use
; the buffer once JIT is off.
opcache.jit = disable
opcache.jit_buffer_size = 0
INI
  chown root:root "${ini_dir}/99-opanel.ini"
  chmod 0644 "${ini_dir}/99-opanel.ini"
  install_ioncube_loader "$version"
  # Enable and start OLS (which manages lsphp)
  restart_openlitespeed 2>/dev/null || true
  echo "LSPHP $version installed successfully"
}

install_ioncube_loader() {
  local version="$1" arch url tmp archive loader target_dir target loader_ini_dir
  require_php_version "$version"
  arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "$arch" in
    amd64|x86_64)
      url="https://downloads.ioncube.com/loader_downloads/ioncube_loaders_lin_x86-64.tar.gz"
      ;;
    *)
      echo "Skipping ionCube Loader: unsupported architecture ${arch}"
      return 0
      ;;
  esac

  apt-get install -y ca-certificates curl tar >/dev/null
  tmp="$(mktemp -d)" || deny "cannot create ionCube temporary directory"
  archive="${tmp}/ioncube_loaders.tar.gz"
  if ! curl -fsSL --connect-timeout 10 --max-time 300 "$url" -o "$archive"; then
    rm -rf -- "$tmp"
    deny "failed to download ionCube Loader"
  fi
  if ! tar -xzf "$archive" -C "$tmp"; then
    rm -rf -- "$tmp"
    deny "failed to unpack ionCube Loader"
  fi
  loader="${tmp}/ioncube/ioncube_loader_lin_${version}.so"
  if [[ ! -f "$loader" ]]; then
    rm -rf -- "$tmp"
    echo "Skipping ionCube Loader: no loader found for PHP ${version}"
    return 0
  fi

  target_dir="/usr/local/ioncube"
  target="${target_dir}/ioncube_loader_lin_${version}.so"
  install -d -o root -g root -m 0755 "$target_dir"
  install -m 0644 -o root -g root "$loader" "$target"
  rm -rf -- "$tmp"

  for loader_ini_dir in /etc/php/"$version"/cli/conf.d /usr/local/lsws/lsphp${version//./}/etc/php/"$version"/mods-available; do
    [[ -d "$loader_ini_dir" ]] || continue
    printf 'zend_extension=%s\n' "$target" >"${loader_ini_dir}/00-ioncube.ini"
    chown root:root "${loader_ini_dir}/00-ioncube.ini"
    chmod 0644 "${loader_ini_dir}/00-ioncube.ini"
  done

  if command -v "php${version}" >/dev/null 2>&1; then
    if ! "php${version}" -v 2>&1 | grep -qi 'ionCube'; then
      rm -f /etc/php/"$version"/cli/conf.d/00-ioncube.ini /usr/local/lsws/lsphp${version//./}/etc/php/"$version"/mods-available/00-ioncube.ini
      deny "ionCube Loader failed to load for PHP ${version}"
    fi
  fi
  echo "ionCube Loader enabled for PHP ${version}"
}

# ---- PHP extensions (PHP config page) ---------------------------------------
# The extension packages the LiteSpeed repository ships per LSPHP version
# (lsphp<XX>-<name>) that the panel offers. Left out: -common/-dev/-dbg/
# -modules-source/-pear, which are not extensions, and -ioncube, because
# install_ioncube_loader manages the loader itself.
PHP_EXT_ALLOWED=(apcu curl igbinary imagick imap intl ldap mailparse memcached msgpack mysql opcache pgsql pspell redis snmp sqlite3 sybase tidy)
# What install_php_version puts on every version, plus igbinary, which redis
# depends on. Sites rely on these, so the panel never removes them.
PHP_EXT_CORE=(curl igbinary imagick intl mysql opcache redis sqlite3)

require_php_ext() {
  local allowed
  for allowed in "${PHP_EXT_ALLOWED[@]}"; do
    [[ "$1" == "$allowed" ]] && return 0
  done
  deny "unsupported PHP extension: $1"
}

php_ext_is_core() {
  local core
  for core in "${PHP_EXT_CORE[@]}"; do
    [[ "$1" == "$core" ]] && return 0
  done
  return 1
}

require_lsphp_installed() {
  [[ -x "/usr/local/lsws/lsphp${1//./}/bin/lsphp" ]] || deny "PHP $1 is not installed"
}

php_ext_package_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

# One line per offered extension -- "ext <name> <installed|available|missing>
# <core|optional>" -- then "module <name>" for every module PHP loads.
php_ext_status() {
  local version="$1" v ext pkg state kind
  require_php_version "$version"
  require_lsphp_installed "$version"
  v="${version//./}"
  for ext in "${PHP_EXT_ALLOWED[@]}"; do
    pkg="lsphp${v}-${ext}"
    if php_ext_package_installed "$pkg"; then
      state=installed
    elif apt-cache show "$pkg" >/dev/null 2>&1; then
      state=available
    else
      state=missing
    fi
    kind=optional
    php_ext_is_core "$ext" && kind=core
    echo "ext ${ext} ${state} ${kind}"
  done
  timeout 20 "/usr/local/lsws/lsphp${v}/bin/php" -m 2>/dev/null | grep -E '^[A-Za-z]' | sed 's/^/module /' || true
}

php_ext_install() {
  local version="$1" ext="$2" pkg
  require_php_version "$version"
  require_lsphp_installed "$version"
  require_php_ext "$ext"
  pkg="lsphp${version//./}-${ext}"
  export DEBIAN_FRONTEND=noninteractive
  if ! apt-cache show "$pkg" >/dev/null 2>&1; then
    apt-get update --allow-releaseinfo-change >/dev/null 2>&1 || true
    apt-cache show "$pkg" >/dev/null 2>&1 || deny "$pkg is not in the LiteSpeed repository"
  fi
  apt-get -o DPkg::Lock::Timeout=120 install -y "$pkg" >/dev/null || deny "apt could not install $pkg"
  restart_openlitespeed
  echo "Installed ${pkg}; PHP ${version} reloaded"
}

# One extension on several PHP versions: a single apt run and one restart.
php_ext_install_all() {
  local ext="$1" version pkg
  shift
  require_php_ext "$ext"
  [[ $# -ge 1 && $# -le 6 ]] || deny "usage: php-ext-install-all <extension> <version>..."
  local packages=()
  for version in "$@"; do
    require_php_version "$version"
    require_lsphp_installed "$version"
    pkg="lsphp${version//./}-${ext}"
    php_ext_package_installed "$pkg" && continue
    packages+=("$pkg")
  done
  if [[ ${#packages[@]} -eq 0 ]]; then
    echo "Already installed"
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  for pkg in "${packages[@]}"; do
    if ! apt-cache show "$pkg" >/dev/null 2>&1; then
      apt-get update --allow-releaseinfo-change >/dev/null 2>&1 || true
      break
    fi
  done
  for pkg in "${packages[@]}"; do
    apt-cache show "$pkg" >/dev/null 2>&1 || deny "$pkg is not in the LiteSpeed repository"
  done
  apt-get -o DPkg::Lock::Timeout=120 install -y "${packages[@]}" >/dev/null || deny "apt could not install ${packages[*]}"
  restart_openlitespeed
  echo "Installed ${packages[*]}; PHP reloaded"
}

php_ext_remove() {
  local version="$1" ext="$2" v pkg name core
  require_php_version "$version"
  require_lsphp_installed "$version"
  require_php_ext "$ext"
  php_ext_is_core "$ext" && deny "$ext is part of the panel's PHP set and cannot be removed"
  v="${version//./}"
  pkg="lsphp${v}-${ext}"
  php_ext_package_installed "$pkg" || deny "$pkg is not installed"
  export DEBIAN_FRONTEND=noninteractive
  # Refuse when apt would take PHP itself or a package the panel needs with it.
  for name in $(apt-get -s remove "$pkg" 2>/dev/null | awk '/^Remv /{print $2}'); do
    [[ "$name" == "lsphp${v}" || "$name" == "lsphp${v}-common" ]] && deny "removing $pkg would also remove $name"
    for core in "${PHP_EXT_CORE[@]}"; do
      [[ "$name" == "lsphp${v}-${core}" ]] && deny "removing $pkg would also remove $name"
    done
  done
  apt-get -o DPkg::Lock::Timeout=120 remove -y "$pkg" >/dev/null || deny "apt could not remove $pkg"
  restart_openlitespeed
  echo "Removed ${pkg}; PHP ${version} reloaded"
}

validate_php_config_file() {
  local file="$1" line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \;* ]] && continue
    case "$line" in *$'\r'*) deny "PHP config contains a carriage return" ;; esac
    [[ "$line" == *"="* ]] || deny "invalid PHP config line: $line"
    key="$(printf '%s' "${line%%=*}" | xargs)"
    value="$(printf '%s' "${line#*=}" | xargs)"
    case "$key" in
      display_errors)
        [[ "$value" == "On" || "$value" == "Off" ]] || deny "invalid display_errors value"
        ;;
      memory_limit|upload_max_filesize|post_max_size)
        [[ "$value" =~ ^[0-9]{1,6}[KMG]?$ ]] || deny "invalid PHP size value for $key"
        ;;
      max_execution_time|max_input_time)
        [[ "$value" =~ ^[0-9]{1,4}$ ]] || deny "invalid integer value for $key"
        (( 10#$value >= 1 && 10#$value <= 3600 )) || deny "$key out of range"
        ;;
      max_input_vars|max_file_uploads)
        [[ "$value" =~ ^[0-9]{1,7}$ ]] || deny "invalid integer value for $key"
        (( 10#$value >= 1 && 10#$value <= 1000000 )) || deny "$key out of range"
        ;;
      opcache.enable|opcache.enable_cli|opcache.validate_timestamps|opcache.save_comments)
        [[ "$value" == "0" || "$value" == "1" ]] || deny "invalid boolean value for $key"
        ;;
      opcache.memory_consumption|opcache.interned_strings_buffer|opcache.max_accelerated_files|opcache.revalidate_freq|lsapi_children|lsapi_max_idle|lsapi_max_idle_children|lsapi_max_process_time)
        [[ "$value" =~ ^[0-9]{1,7}$ ]] || deny "invalid integer value for $key"
        ;;
      opcache.jit)
        [[ "$value" =~ ^[A-Za-z0-9_-]{0,32}$ ]] || deny "invalid opcache.jit value"
        ;;
      opcache.jit_buffer_size)
        [[ "$value" =~ ^[0-9]{1,6}M?$ ]] || deny "invalid opcache.jit_buffer_size value"
        ;;
      *)
        deny "unsupported PHP config directive: $key"
        ;;
    esac
  done <"$file"
}

write_php_config() {
  local version="$1" conf_dir target tmp size
  require_php_version "$version"
  conf_dir="/usr/local/lsws/lsphp${version//./}/etc/php/${version}/mods-available"
  target="${conf_dir}/99-opanel.ini"
  install -d -o root -g root -m 0755 "$conf_dir"
  tmp="$(mktemp "${conf_dir}/.99-opanel.ini.XXXXXX")" || deny "cannot create temporary PHP config"
  if ! cat >"$tmp"; then
    rm -f -- "$tmp"
    deny "failed to read PHP config"
  fi
  size="$(wc -c <"$tmp" | tr -d '[:space:]')"
  if (( size <= 0 || size > 8192 )); then
    rm -f -- "$tmp"
    deny "PHP config size out of range"
  fi
  validate_php_config_file "$tmp"
  chown root:root "$tmp"
  chmod 0644 "$tmp"
  mv -f -- "$tmp" "$target"
  restart_openlitespeed
  echo "PHP ${version} config updated: ${target}"
}

waf_status() {
  echo "ModSecurity module:"
  if /usr/local/lsws/bin/lswsctrl status 2>&1 | grep -qi modsecurity || [[ -d /usr/local/lsws/conf/opanel/waf ]]; then
    echo "  installed"
  else
    echo "  not installed"
  fi
  echo "Rules file:"
  [[ -f /usr/local/lsws/conf/opanel/waf/opanel-main.conf ]] && echo "  /usr/local/lsws/conf/opanel/waf/opanel-main.conf" || echo "  missing"
  echo "Default rules:"
  [[ -f /usr/local/lsws/conf/opanel/waf/opanel-default.conf ]] && echo "  /usr/local/lsws/conf/opanel/waf/opanel-default.conf" || echo "  missing"
  echo "Custom rules:"
  [[ -f /usr/local/lsws/conf/opanel/waf/opanel-custom.conf ]] && echo "  /usr/local/lsws/conf/opanel/waf/opanel-custom.conf" || echo "  missing"
  echo "Managed profile:"
  echo "  opanel built-in lightweight WordPress/Laravel/PHP rules"
  echo "Timers:"
  systemctl list-timers opanel-auto-update.timer apt-daily-upgrade.timer --no-pager 2>/dev/null || true
}

audit_log() {
  local quoted="" arg
  for arg in "$@"; do
    printf -v quoted '%s %q' "$quoted" "$arg"
  done
  if command -v logger >/dev/null 2>&1; then
    logger -t opanel-helper -- "cmd=${cmd:-unknown}${quoted}"
  fi
}

run_ip_rule() {
  local action="$1" network="$2" port="${3:-}" protocol="${4:-tcp}"
  require_ip_or_cidr "$network"
  case "$action" in
    allow|deny) ;;
    *) deny "invalid firewall action: $action" ;;
  esac
  local target
  if [[ "$action" == "allow" ]]; then
    target="ACCEPT"
  else
    target="DROP"
  fi
  if [[ -z "$port" ]]; then
    iptables -A OPANEL_USER -s "$network" -j "$target" -m comment --comment "opanel:UserZone" 2>/dev/null \
      || iptables -A OPANEL_USER -s "$network" -j "$target" 2>/dev/null \
      || true
    return 0
  fi
  require_port "$port"; require_proto "$protocol"
  iptables -A OPANEL_USER -s "$network" -p "$protocol" --dport "$port" -j "$target" -m comment --comment "opanel:UserZone" 2>/dev/null \
    || iptables -A OPANEL_USER -s "$network" -p "$protocol" --dport "$port" -j "$target" 2>/dev/null \
    || true
}

require_url() {
  local value="$1"
  [[ "$value" =~ ^https?://[^[:space:]]+$ ]] || deny "invalid URL: $value"
}

firewall_blocklist_urls() {
  ensure_opanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  sed '/^[[:space:]]*$/d' "$FIREWALL_BLOCKLIST_URLS" | sort -u
}

firewall_blocklist_write_timer() {
  cat >/etc/systemd/system/opanel-firewall-blocklist.service <<SERVICE
[Unit]
Description=Refresh opanel IP blocklists
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=SUDO_USER=opanel
ExecStart=/usr/local/sbin/opanel-helper blocklist-run
SERVICE
  cat >/etc/systemd/system/opanel-firewall-blocklist.timer <<TIMER
[Unit]
Description=Refresh opanel IP blocklists daily

[Timer]
OnCalendar=*-*-* 01:00:00
Persistent=true

[Install]
WantedBy=timers.target
TIMER
  systemctl daemon-reload
  systemctl enable --now opanel-firewall-blocklist.timer >/dev/null 2>&1 || true
}

firewall_blocklist_apply() {
  local v4_count=0 v6_count=0 v4_max=65536 v6_max=65536
  local v4_new="${BLOCKLIST_IPSET_V4}_new" v6_new="${BLOCKLIST_IPSET_V6}_new"
  local v4_target="$v4_new" v6_target="$v6_new"
  ensure_ols_conf_dir_writable
  install -d -o root -g root -m 0755 "$BLOCKLIST_DIR"
  iptables -N OPANEL_BLOCKLIST 2>/dev/null || true
  ip6tables -N OPANEL_BLOCKLIST 2>/dev/null || true
  iptables -C INPUT -j OPANEL_BLOCKLIST 2>/dev/null || iptables -I INPUT 1 -j OPANEL_BLOCKLIST 2>/dev/null || true
  ip6tables -C INPUT -j OPANEL_BLOCKLIST 2>/dev/null || ip6tables -I INPUT 1 -j OPANEL_BLOCKLIST 2>/dev/null || true
  if [[ -s "${BLOCKLIST_DIR}/blocklist.set" ]]; then
    v4_count="$(awk 'NF && $0 !~ /^[[:space:]]*#/ && index($0, ":") == 0 { count++ } END { print count + 0 }' "${BLOCKLIST_DIR}/blocklist.set")"
    v6_count="$(awk 'NF && $0 !~ /^[[:space:]]*#/ && index($0, ":") > 0 { count++ } END { print count + 0 }' "${BLOCKLIST_DIR}/blocklist.set")"
    v4_max=$(( v4_count + v4_count / 4 + 1024 ))
    v6_max=$(( v6_count + v6_count / 4 + 1024 ))
    (( v4_max < 65536 )) && v4_max=65536
    (( v6_max < 65536 )) && v6_max=65536
  fi
  ipset create "$BLOCKLIST_IPSET_V4" hash:net family inet hashsize 32768 maxelem "$v4_max" -exist 2>/dev/null || true
  ipset create "$BLOCKLIST_IPSET_V6" hash:net family inet6 hashsize 32768 maxelem "$v6_max" -exist 2>/dev/null || true
  ipset destroy "$v4_new" 2>/dev/null || true
  ipset destroy "$v6_new" 2>/dev/null || true
  ipset create "$v4_new" hash:net family inet hashsize 32768 maxelem "$v4_max" 2>/dev/null || true
  ipset create "$v6_new" hash:net family inet6 hashsize 32768 maxelem "$v6_max" 2>/dev/null || true
  if ! ipset list "$v4_new" >/dev/null 2>&1; then
    v4_target="$BLOCKLIST_IPSET_V4"
    ipset flush "$v4_target" 2>/dev/null || true
  fi
  if ! ipset list "$v6_new" >/dev/null 2>&1; then
    v6_target="$BLOCKLIST_IPSET_V6"
    ipset flush "$v6_target" 2>/dev/null || true
  fi
  if [[ -s "${BLOCKLIST_DIR}/blocklist.set" ]]; then
    # A per-line `ipset add` loop forks one process per entry; with a
    # 100k+ line blocklist that turns a few seconds of work into minutes,
    # and this function can run several times in one "Update panel now"
    # pass. `ipset restore` loads the whole batch in a single process.
    awk -v v4="$v4_target" -v v6="$v6_target" '
      NF && $0 !~ /^[[:space:]]*#/ {
        # Re-apply the prefix floor at the load site: /var/lib/opanel is
        # opanel-owned, so blocklist.set can be written without going through
        # the fetch filter above.
        slash = index($0, "/")
        prefix = (slash ? substr($0, slash + 1) + 0 : 128)
        if (index($0, ":") > 0) { if (prefix < 16) next; print "add " v6 " " $0 }
        else { if (prefix < 8) next; print "add " v4 " " $0 }
      }
    ' "${BLOCKLIST_DIR}/blocklist.set" | ipset restore -exist 2>/dev/null || true
  fi
  if [[ "$v4_target" == "$v4_new" ]]; then
    ipset swap "$v4_new" "$BLOCKLIST_IPSET_V4" 2>/dev/null || true
    ipset destroy "$v4_new" 2>/dev/null || true
  fi
  if [[ "$v6_target" == "$v6_new" ]]; then
    ipset swap "$v6_new" "$BLOCKLIST_IPSET_V6" 2>/dev/null || true
    ipset destroy "$v6_new" 2>/dev/null || true
  fi
  if ! iptables -C OPANEL_BLOCKLIST -m set --match-set "$BLOCKLIST_IPSET_V4" src -j DROP 2>/dev/null; then
    iptables -I OPANEL_BLOCKLIST 1 -m set --match-set "$BLOCKLIST_IPSET_V4" src -j DROP 2>/dev/null || true
  fi
  if ! ip6tables -C OPANEL_BLOCKLIST -m set --match-set "$BLOCKLIST_IPSET_V6" src -j DROP 2>/dev/null; then
    ip6tables -I OPANEL_BLOCKLIST 1 -m set --match-set "$BLOCKLIST_IPSET_V6" src -j DROP 2>/dev/null || true
  fi
  # Loopback leaves this chain before the DROP can see it. This chain is jumped
  # from INPUT at position 1, ahead of the loopback and ESTABLISHED accepts in
  # OPANEL_INPUT, so a blocklist entry wide enough to cover 127.0.0.1 would
  # otherwise cut the panel's own health check and the phpMyAdmin SSO call to
  # 127.0.0.1. The prefix floor above should make that unreachable; this is the
  # second line of defence, since a remote list is the input.
  if ! iptables -C OPANEL_BLOCKLIST -i lo -j RETURN 2>/dev/null; then
    iptables -I OPANEL_BLOCKLIST 1 -i lo -j RETURN 2>/dev/null || true
  fi
  if ! ip6tables -C OPANEL_BLOCKLIST -i lo -j RETURN 2>/dev/null; then
    ip6tables -I OPANEL_BLOCKLIST 1 -i lo -j RETURN 2>/dev/null || true
  fi
  ensure_firewall_rule_store >/dev/null 2>&1 || true
}

firewall_blocklist_status() {
  ensure_opanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  echo "URLs:"
  if [[ -s "$FIREWALL_BLOCKLIST_URLS" ]]; then
    firewall_blocklist_urls | sed 's/^/  /'
  else
    echo "  (none)"
  fi
  echo ""
  echo "Engine:"
  echo "  ipset"
  echo "Rules file:"
  [[ -f "${BLOCKLIST_DIR}/blocklist.set" ]] && echo "  ${BLOCKLIST_DIR}/blocklist.set" || echo "  missing"
  echo ""
  echo "ipset:"
  local total4 total6
  total4="$(ipset list "$BLOCKLIST_IPSET_V4" 2>/dev/null | grep -Ec '^[0-9]' || true)"
  total6="$(ipset list "$BLOCKLIST_IPSET_V6" 2>/dev/null | grep -Ec '^[0-9a-fA-F:]+/' || true)"
  echo "  ${BLOCKLIST_IPSET_V4}: ${total4:-0} network(s)"
  echo "  ${BLOCKLIST_IPSET_V6}: ${total6:-0} network(s)"
  echo ""
  echo "Timer:"
  systemctl is-enabled opanel-firewall-blocklist.timer 2>/dev/null || true
  systemctl list-timers opanel-firewall-blocklist.timer --no-pager 2>/dev/null || true
}

firewall_blocklist_clear_rules() {
  ipset flush "$BLOCKLIST_IPSET_V4" 2>/dev/null || true
  ipset flush "$BLOCKLIST_IPSET_V6" 2>/dev/null || true
}

firewall_blocklist_run() {
  ensure_opanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  local tmp fetched rules_tmp count url old_work old_rules
  tmp="$(mktemp)"
  fetched="$(mktemp)"
  rules_tmp="$(mktemp)"
  old_work="$(mktemp)"
  old_rules="$(mktemp)"
  [[ -f "$FIREWALL_BLOCKLIST_WORK" ]] && cp "$FIREWALL_BLOCKLIST_WORK" "$old_work" || true
  [[ -f "${BLOCKLIST_DIR}/blocklist.set" ]] && cp "${BLOCKLIST_DIR}/blocklist.set" "$old_rules" || true
  local fetch_failures=0
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    require_url "$url"
    if ! curl -fsSL --connect-timeout 10 --max-time 30 "$url" >>"$fetched"; then
      echo "WARNING: could not fetch $url" >&2
      fetch_failures=$((fetch_failures + 1))
    fi
    printf '\n' >>"$fetched"
  done < <(firewall_blocklist_urls)
  python3 - "$fetched" "$tmp" "$rules_tmp" <<'PY'
import ipaddress
import re
import sys

seen = set()
networks = []
for raw in open(sys.argv[1], encoding="utf-8", errors="ignore"):
    line = re.split(r"[\s#;,]+", raw.strip(), 1)[0]
    if not line:
        continue
    try:
        value = str(ipaddress.ip_network(line, strict=False))
    except ValueError:
        continue
    network = ipaddress.ip_network(value, strict=False)
    if (
        network.is_loopback
        or network.is_private
        or network.is_link_local
        or network.is_multicast
        or network.is_reserved
        or network.is_unspecified
    ):
        continue
    # A prefix floor. None of the flags above reject 0.0.0.0/1 or 0.0.0.0/0 --
    # they are neither private nor reserved nor loopback as a *network* -- yet
    # 0.0.0.0/1 contains 127.0.0.1, and OPANEL_BLOCKLIST is jumped from INPUT
    # at position 1, ahead of the loopback and ESTABLISHED accepts. One such
    # line in a subscribed list therefore DROPs every inbound packet including
    # loopback: panel, SSH, web, mail and the panel's own health check, saved
    # by firewall_persist_rules and re-armed after reboot, recoverable only
    # from an out-of-band console. No real blocklist entry needs to be this
    # wide; /8 and /16 already cover 16.7M and 2^112 addresses.
    if (network.version == 4 and network.prefixlen < 8) or (
        network.version == 6 and network.prefixlen < 16
    ):
        continue
    if value not in seen:
        seen.add(value)
        networks.append(value)

with open(sys.argv[2], "w", encoding="utf-8") as handle:
    for value in networks:
        handle.write(value + "\n")

with open(sys.argv[3], "w", encoding="utf-8") as handle:
    handle.write("# Managed by opanel. Generated from URL IP blocklists.\n")
    handle.write("# Loaded into opanel_blocklist4/opanel_blocklist6 ipsets.\n")
    for value in networks:
        handle.write(value + "\n")
PY
  # A transient DNS or network failure used to overwrite the stored list with
  # an empty one and flush the ipset, so one bad night wiped every blocked
  # network until the next successful run. Keep what we have instead.
  local new_count old_count
  new_count="$(sed '/^[[:space:]]*$/d' "$tmp" | wc -l | tr -d '[:space:]')"
  old_count="$(sed '/^[[:space:]]*$/d' "$old_work" 2>/dev/null | wc -l | tr -d '[:space:]')"
  if (( fetch_failures > 0 )) && (( new_count * 2 < old_count )); then
    rm -f "$tmp" "$fetched" "$rules_tmp" "$old_work" "$old_rules"
    deny "blocklist refresh aborted: ${fetch_failures} source(s) unreachable and the result (${new_count}) is far below the stored list (${old_count}); keeping the existing blocklist"
  fi
  install -d -o root -g root -m 0755 "$BLOCKLIST_DIR"
  install -m 0644 -o root -g root "$rules_tmp" "${BLOCKLIST_DIR}/blocklist.set"
  install -m 0644 -o root -g root "$tmp" "$FIREWALL_BLOCKLIST_WORK"
  firewall_blocklist_apply
  count="$(sed '/^[[:space:]]*$/d' "$FIREWALL_BLOCKLIST_WORK" | wc -l | tr -d '[:space:]')"
  firewall_blocklist_write_timer
  rm -f "$tmp" "$fetched" "$rules_tmp" "$old_work" "$old_rules"
  echo "Blocklist refreshed: ${count} network(s)"
}

firewall_blocklist_add_url() {
  local url="$1"
  require_url "$url"
  ensure_opanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  if ! grep -Fxq -- "$url" "$FIREWALL_BLOCKLIST_URLS"; then
    printf '%s\n' "$url" >>"$FIREWALL_BLOCKLIST_URLS"
  fi
  sort -u -o "$FIREWALL_BLOCKLIST_URLS" "$FIREWALL_BLOCKLIST_URLS"
  firewall_blocklist_write_timer
  echo "Blocklist URL added"
}

firewall_blocklist_delete_url() {
  local url="$1"
  require_url "$url"
  ensure_opanel_data_dir
  touch "$FIREWALL_BLOCKLIST_URLS"
  grep -Fxv -- "$url" "$FIREWALL_BLOCKLIST_URLS" >"${FIREWALL_BLOCKLIST_URLS}.tmp" || true
  mv -f "${FIREWALL_BLOCKLIST_URLS}.tmp" "$FIREWALL_BLOCKLIST_URLS"
  firewall_blocklist_write_timer
  echo "Blocklist URL removed"
}

write_ssl_auto_renew_timer() {
  cat >/etc/systemd/system/opanel-ssl-auto-renew.service <<SERVICE
[Unit]
Description=Renew opanel SSL certificates that expire within 10 days
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=SUDO_USER=opanel
ExecStart=/usr/local/sbin/opanel-helper certbot-renew-soon 10
SERVICE
  cat >/etc/systemd/system/opanel-ssl-auto-renew.timer <<TIMER
[Unit]
Description=Check opanel SSL certificates daily

[Timer]
OnCalendar=*-*-* 01:30:00
Persistent=true

[Install]
WantedBy=timers.target
TIMER
  systemctl daemon-reload
  systemctl enable --now opanel-ssl-auto-renew.timer >/dev/null 2>&1 || true
}

copy_panel_live_certificate() {
  local domain="$1"
  [[ -n "$domain" ]] || return 0
  is_domain "$domain" || return 0
  # /etc/opanel/panel-*.pem is the panel's OWN certificate -- it backs the panel
  # port and the phpMyAdmin tools vhost, which are served for the panel hostname
  # only. A website's "Install SSL" (certbot-issue <site-domain>) must never
  # overwrite it, or phpMyAdmin SSO breaks with an SNI cert mismatch. Only copy
  # when the issued domain IS the panel's hostname.
  local panel_host
  panel_host="$(env_get PANEL_DOMAIN)"
  if [[ -z "$panel_host" ]]; then
    panel_host="$(env_get PANEL_URL)"
    panel_host="${panel_host#*://}"; panel_host="${panel_host%%/*}"; panel_host="${panel_host%%:*}"
  fi
  [[ -n "$panel_host" && "$domain" == "$panel_host" ]] || return 0
  [[ "$(panel_url_scheme)" == "https" ]] || return 0
  [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" && -f "/etc/letsencrypt/live/${domain}/privkey.pem" ]] || return 0
  install -d -o root -g opanel -m 0750 /etc/opanel
  install -m 0640 -o root -g opanel "/etc/letsencrypt/live/${domain}/fullchain.pem" /etc/opanel/panel-fullchain.pem
  install -m 0640 -o root -g opanel "/etc/letsencrypt/live/${domain}/privkey.pem" /etc/opanel/panel-privkey.pem
  if [[ -f "$ENV_FILE" ]]; then
    env_set PANEL_SSL_CERT "/etc/opanel/panel-fullchain.pem"
    env_set PANEL_SSL_KEY "/etc/opanel/panel-privkey.pem"
  fi
}

# ---- panel certificate store -----------------------------------------------
# The panel serves HTTPS itself and picks a certificate per SNI hostname, so it
# needs every site certificate in a directory it can read. It runs as `opanel`
# and /etc/letsencrypt is root-only (0700), so certificates are mirrored here as
# root:opanel 0640 -- the same arrangement panel-ssl-install already used for the
# single panel certificate, just one directory per domain.
PANEL_CERT_STORE="/etc/opanel/certs"

panel_cert_store_put() {
  local name="$1" cert="$2" key="$3"
  [[ -f "$cert" && -f "$key" ]] || return 0
  install -d -o root -g opanel -m 0750 "$PANEL_CERT_STORE"
  install -d -o root -g opanel -m 0750 "${PANEL_CERT_STORE}/${name}"
  install -m 0640 -o root -g opanel "$cert" "${PANEL_CERT_STORE}/${name}/fullchain.pem"
  install -m 0640 -o root -g opanel "$key" "${PANEL_CERT_STORE}/${name}/privkey.pem"
}

# A self-signed fallback so the panel is never served over plain HTTP. Browsers
# will warn on it, which is correct: it is a placeholder until a real
# certificate exists, not a substitute for one.
panel_self_signed_ensure() {
  local dir="${PANEL_CERT_STORE}/_default"
  local cert="${dir}/fullchain.pem" key="${dir}/privkey.pem"
  if [[ -f "$cert" && -f "$key" ]] && openssl x509 -checkend 2592000 -noout -in "$cert" >/dev/null 2>&1; then
    return 0
  fi
  local cn tmp
  cn="$(env_get PANEL_DOMAIN)"
  [[ -n "$cn" ]] || cn="$(hostname -f 2>/dev/null || hostname)"
  [[ -n "$cn" ]] || cn="opanel.local"
  tmp="$(mktemp -d /tmp/opanel-selfsigned.XXXXXX)"
  if openssl req -x509 -newkey rsa:2048 -nodes -days 3650        -keyout "${tmp}/privkey.pem" -out "${tmp}/fullchain.pem"        -subj "/CN=${cn}" -addext "subjectAltName=DNS:${cn}" >/dev/null 2>&1; then
    panel_cert_store_put "_default" "${tmp}/fullchain.pem" "${tmp}/privkey.pem"
    echo "Generated self-signed panel certificate for ${cn}"
  else
    echo "WARNING: could not generate a self-signed panel certificate" >&2
  fi
  rm -rf "$tmp"
}

# Mirror every Let's Encrypt certificate into the store and drop entries whose
# certificate is gone, so a site that lost its SSL stops being offered a stale one.
panel_cert_store_sync() {
  local live name
  panel_self_signed_ensure
  install -d -o root -g opanel -m 0750 "$PANEL_CERT_STORE"
  for live in /etc/letsencrypt/live/*/; do
    [[ -d "$live" ]] || continue
    name="$(basename "$live")"
    is_domain "$name" || continue
    panel_cert_store_put "$name" "${live}fullchain.pem" "${live}privkey.pem"
  done
  for live in "${PANEL_CERT_STORE}"/*/; do
    [[ -d "$live" ]] || continue
    name="$(basename "$live")"
    [[ "$name" == "_default" ]] && continue
    if [[ ! -f "/etc/letsencrypt/live/${name}/fullchain.pem" ]]; then
      rm -rf "${PANEL_CERT_STORE:?}/${name}"
    fi
  done
  mail_tls_sync
}

install_manual_ssl() {
  local domain="$1" base tmpdir
  require_domain "$domain"
  base="/usr/local/lsws/conf/opanel/ssl/sites/${domain}"
  tmpdir="$(mktemp -d /tmp/opanel-manual-ssl.XXXXXX)"
  trap 'rm -rf "${tmpdir:-}"; trap - RETURN' RETURN
  local payload_file="$tmpdir/payload.json"
  cat >"$payload_file"
  python3 - "$tmpdir" "$payload_file" <<'PY'
import json
import pathlib
import sys

tmpdir = pathlib.Path(sys.argv[1])
payload_file = pathlib.Path(sys.argv[2])
data = json.loads(payload_file.read_text(encoding="utf-8"))
parts = {
    "cert.crt": data.get("certificate", ""),
    "privkey.key": data.get("private_key", ""),
}
ca_bundle = data.get("ca_bundle", "")
if ca_bundle:
    parts["ca.crt"] = ca_bundle
for name, content in parts.items():
    if not content or "\x00" in content:
        raise SystemExit(f"invalid {name}")
    (tmpdir / name).write_text(content, encoding="utf-8")
PY
  install -d -o root -g opanel -m 0750 "$base"
  install -m 0640 -o root -g opanel "$tmpdir/cert.crt" "$base/cert.crt"
  install -m 0640 -o root -g opanel "$tmpdir/privkey.key" "$base/privkey.key"
  if [[ -f "$tmpdir/ca.crt" ]]; then
    install -m 0640 -o root -g opanel "$tmpdir/ca.crt" "$base/ca.crt"
    cat "$tmpdir/cert.crt" "$tmpdir/ca.crt" >"$tmpdir/fullchain.crt"
    install -m 0640 -o root -g opanel "$tmpdir/fullchain.crt" "$base/fullchain.crt"
  else
    rm -f "$base/ca.crt"
    install -m 0640 -o root -g opanel "$tmpdir/cert.crt" "$base/fullchain.crt"
  fi
  echo "Manual SSL installed for ${domain}"
}

remove_manual_ssl() {
  local domain="$1" base
  require_domain "$domain"
  base="/usr/local/lsws/conf/opanel/ssl/sites/${domain}"
  rm -f "$base/cert.crt" "$base/privkey.key" "$base/ca.crt" "$base/fullchain.crt"
  rmdir "$base" 2>/dev/null || true
  echo "Manual SSL removed for ${domain}"
}

renew_ssl_soon() {
  local days="${1:-10}" seconds cert cert_name checked=0 renewed=0 panel_domain
  [[ "$days" =~ ^[0-9]+$ && "$days" -ge 1 && "$days" -le 30 ]] || deny "usage: certbot-renew-soon [1-30 days]"
  write_ssl_auto_renew_timer
  if ! command -v certbot >/dev/null 2>&1; then
    echo "certbot is not installed"
    return 0
  fi
  seconds=$((days * 86400))
  shopt -s nullglob
  for cert in /etc/letsencrypt/live/*/cert.pem; do
    [[ -f "$cert" ]] || continue
    cert_name="$(basename "$(dirname "$cert")")"
    [[ "$cert_name" == "README" ]] && continue
    checked=$((checked + 1))
    if ! openssl x509 -checkend "$seconds" -noout -in "$cert" >/dev/null 2>&1; then
      echo "Renewing certificate: ${cert_name}"
      if certbot renew --cert-name "$cert_name" --quiet --force-renewal \
        --deploy-hook "systemctl restart lshttpd.service || /usr/local/lsws/bin/lswsctrl restart || true; systemctl restart opanel-api || true"; then
        renewed=$((renewed + 1))
      else
        echo "WARNING: could not renew ${cert_name}" >&2
      fi
    fi
  done
  shopt -u nullglob
  panel_domain="$(env_get PANEL_DOMAIN)"
  copy_panel_live_certificate "$panel_domain"
  panel_cert_store_sync
  if [[ "$renewed" -gt 0 ]]; then
    restart_openlitespeed >/dev/null 2>&1 || true
    systemctl restart opanel-api >/dev/null 2>&1 || true
  fi
  echo "SSL auto-renew checked ${checked} certificate(s); renewed ${renewed} certificate(s) within ${days} day(s)."
}

# Cloudflare DNS-01 plugin — installed on demand so a box that never issues a
# wildcard cert stays lean.
ACME_DNS_DIR="/etc/opanel/acme-dns"

ensure_certbot_dns_cloudflare() {
  if certbot plugins 2>/dev/null | grep -q 'dns-cloudflare'; then
    return 0
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-certbot-dns-cloudflare >/dev/null 2>&1 || true
  certbot plugins 2>/dev/null | grep -q 'dns-cloudflare' \
    || deny "certbot-dns-cloudflare plugin is not available (install python3-certbot-dns-cloudflare)"
}

# Issue / renew a wildcard cert for <domain> + *.<domain> via Cloudflare DNS-01.
# stdin is a scoped Cloudflare API Token (Zone:DNS:Edit for the zone) -- NOT the
# Global API Key. It is written as `dns_cloudflare_api_token` in a root-only
# credentials file that certbot re-reads on every renewal.
issue_cloudflare_wildcard() {
  local domain="$1" email="${2:-}" token creds
  require_domain "$domain"
  if [[ -n "$email" ]]; then
    require_email "$email"
  fi
  token="$(cat)"
  token="${token%$'\n'}"
  [[ -n "$token" ]] || deny "empty Cloudflare API token"
  [[ "$token" =~ ^[A-Za-z0-9_-]{20,200}$ ]] || deny "malformed Cloudflare API token"
  ensure_certbot_dns_cloudflare
  install -d -o root -g root -m 0700 "$ACME_DNS_DIR"
  creds="${ACME_DNS_DIR}/${domain}.ini"
  local tmp
  tmp="$(mktemp "${ACME_DNS_DIR}/.${domain}.XXXXXX")"
  printf 'dns_cloudflare_api_token = %s\n' "$token" >"$tmp"
  install -o root -g root -m 0600 "$tmp" "$creds"
  rm -f "$tmp"
  local args=(certonly --dns-cloudflare
    --dns-cloudflare-credentials "$creds"
    --dns-cloudflare-propagation-seconds 30
    --cert-name "$domain" --non-interactive --agree-tos --expand
    --deploy-hook "systemctl restart lshttpd.service 2>/dev/null || /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || true; systemctl restart opanel-api 2>/dev/null || true"
    -d "$domain" -d "*.${domain}")
  if [[ -n "$email" ]]; then
    args+=(--email "$email")
  else
    args+=(--register-unsafely-without-email)
  fi
  certbot "${args[@]}"
  restart_openlitespeed
  copy_panel_live_certificate "$domain"
  panel_cert_store_sync
  echo "Wildcard SSL certificate issued for ${domain} (and *.${domain})"
}

remove_cloudflare_wildcard() {
  local domain="$1"
  require_domain "$domain"
  certbot delete --cert-name "$domain" --non-interactive 2>/dev/null || true
  rm -f "${ACME_DNS_DIR}/${domain}.ini"
  panel_cert_store_sync
  echo "Wildcard SSL certificate removed for ${domain}"
}

is_in() {
  local needle="$1"; shift
  local x
  for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done
  return 1
}

is_allowed_service() {
  local service="$1" php_version=""
  if is_in "$service" "${ALLOWED_SERVICES[@]}"; then
    return 0
  fi
  return 1
}

require_safe_path() {
  local prefix="$1" path="$2"
  # Reject path traversal components, newlines, and empty input. Bash strings
  # cannot carry NUL bytes, so there is no separate NUL pattern here.
  # Note: we cannot use `*..*` as a glob because that would also reject
  # legitimate filenames that just happen to contain a dot adjacent to a dot
  # via Bash's pattern matching quirks; instead we match the `..` only when
  # it actually forms a path component.
  case "$path" in
    *$'\n'*) deny "unsafe path: $path" ;;
    "") deny "empty path" ;;
    "..") deny "path traversal not allowed" ;;
    "../"*|*"/.."|*"/../"*) deny "path traversal not allowed" ;;
  esac
  local resolved
  resolved=$(readlink -m "$path") || deny "cannot resolve $path"
  case "$resolved/" in
    "$prefix"/*) ;;
    *) deny "path outside $prefix: $resolved" ;;
  esac
  echo "$resolved"
}

require_domain() {
  local d="$1"
  [[ "$d" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] \
    || deny "invalid domain: $d"
}

require_email() {
  local e="$1"
  [[ "$e" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] \
    || deny "invalid email: $e"
}

require_port() {
  [[ "$1" =~ ^[0-9]{1,5}$ ]] || deny "invalid port: $1"
  (( $1 >= 1 && $1 <= 65535 )) || deny "port out of range: $1"
}

require_tail_lines() {
  [[ "$1" =~ ^[0-9]{1,4}$ ]] || deny "invalid log line count: $1"
  (( $1 >= 1 && $1 <= 5000 )) || deny "log line count out of range: $1"
}

require_proto() {
  [[ "$1" == "tcp" || "$1" == "udp" ]] || deny "invalid protocol: $1"
}

require_php_version() {
  [[ "$1" =~ ^(7\.4|8\.1|8\.2|8\.3|8\.4|8\.5)$ ]] || deny "invalid PHP version: $1"
}

require_linux_user() {
  [[ "$1" =~ ^[a-z_][a-z0-9_-]{2,31}$ ]] || deny "invalid panel Linux user: $1"
  case "$1" in
    root|daemon|bin|sys|sync|games|man|lp|mail|news|uucp|proxy|www-data|backup|list|irc|_apt|nobody|opanel|opanel-sites|opanel-sftp|mysql|redis|nobody)
      deny "reserved panel Linux user: $1" ;;
  esac
}

require_site_domain_segment() {
  [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] \
    || deny "invalid site domain path segment: $1"
}

# 0711 root:root: every site user may pass through to its own directory, none
# may list which sites exist. Each site's directory is 0750 and its own, so
# tenants cannot read each other's errors (file paths, SQL, sometimes secrets).
ensure_php_log_dir() {
  local domain="$1" user="$2"
  install -d -o root -g root -m 0711 "$PHP_LOG_ROOT"
  chmod 0711 "$PHP_LOG_ROOT"
  install -d -o "$user" -g "$user" -m 0750 "${PHP_LOG_ROOT}/${domain}"
}

read_site_log() {
  local domain="$1" kind="$2" lines="$3" resolved php_log php_resolved any=0
  require_domain "$domain"
  [[ "$kind" == "access" || "$kind" == "error" ]] || deny "invalid log kind: $kind"
  require_tail_lines "$lines"
  resolved=$(readlink -m "/var/log/openlitespeed/${domain}.${kind}.log") || deny "cannot resolve log path"
  case "$resolved" in
    /var/log/openlitespeed/*) ;;
    *) deny "log path outside /var/log/openlitespeed: $resolved" ;;
  esac

  # The Error tab shows PHP's own error_log first (that is where application
  # errors land), then the OpenLiteSpeed server error log.
  if [[ "$kind" == "error" ]]; then
    php_resolved=$(readlink -m "${PHP_LOG_ROOT}/${domain}/php_error.log") || deny "cannot resolve log path"
    case "$php_resolved" in
      "${PHP_LOG_ROOT}"/*) ;;
      *) deny "log path outside ${PHP_LOG_ROOT}: $php_resolved" ;;
    esac
    echo "opanel_LOG_PATH=${php_resolved} + ${resolved}" >&2
    if [[ -s "$php_resolved" ]]; then
      any=1
      echo "===== PHP error log (${php_resolved}) ====="
      tail -n "$lines" -- "$php_resolved"
      echo
    fi
    if [[ -s "$resolved" ]]; then
      any=1
      echo "===== OpenLiteSpeed error log (${resolved}) ====="
      tail -n "$lines" -- "$resolved"
    fi
    [[ "$any" == 1 ]] || echo "opanel_LOG_MISSING=1" >&2
    return 0
  fi

  echo "opanel_LOG_PATH=$resolved" >&2
  if [[ ! -f "$resolved" ]]; then
    echo "opanel_LOG_MISSING=1" >&2
    return 0
  fi
  tail -n "$lines" -- "$resolved"
}

read_waf_access_logs() {
  local lines="$1" domain path resolved
  shift
  require_tail_lines "$lines"
  [[ $# -ge 1 ]] || deny "usage: waf-access-log-read <lines> <domain>..."
  for domain in "$@"; do
    require_domain "$domain"
    path="/var/log/openlitespeed/${domain}.access.log"
    resolved=$(readlink -m "$path") || deny "cannot resolve log path"
    case "$resolved" in
      /var/log/openlitespeed/*) ;;
      *) deny "log path outside /var/log/openlitespeed: $resolved" ;;
    esac
    printf 'opanel_LOG_PATH=%s\t%s\n' "$domain" "$resolved"
    if [[ -f "$resolved" ]]; then
      tail -n "$lines" -- "$resolved" | sed "s/^/${domain}\t/"
    fi
  done
}

clear_waf_access_logs() {
  local domain path resolved
  [[ $# -ge 1 ]] || deny "usage: waf-access-log-clear <domain>..."
  for domain in "$@"; do
    require_domain "$domain"
    path="/var/log/openlitespeed/${domain}.access.log"
    resolved=$(readlink -m "$path") || deny "cannot resolve log path"
    case "$resolved" in
      /var/log/openlitespeed/*) ;;
      *) deny "log path outside /var/log/openlitespeed: $resolved" ;;
    esac
    if [[ -f "$resolved" ]]; then
      : >"$resolved"
    fi
  done
  echo "WAF access logs cleared"
}

require_managed_path() {
  local path="$1" user="${2:-}"
  local resolved first_part relative domain_part
  resolved=$(require_safe_path "$HOME_ROOT" "$path")
  if [[ -n "$user" ]]; then
    require_linux_user "$user"
    case "$resolved/" in
      "$HOME_ROOT/$user/"*)
        relative="${resolved#${HOME_ROOT}/${user}/}"
        domain_part="${relative%%/*}"
        require_site_domain_segment "$domain_part"
        ;;
      *) deny "path is not owned by panel Linux user $user: $resolved" ;;
    esac
  else
    case "$resolved/" in
      "$HOME_ROOT"/*/*)
        first_part="${resolved#${HOME_ROOT}/}"
        first_part="${first_part%%/*}"
        require_linux_user "$first_part"
        relative="${resolved#${HOME_ROOT}/${first_part}/}"
        domain_part="${relative%%/*}"
        require_site_domain_segment "$domain_part"
        ;;
      *) deny "path outside managed site roots: $resolved" ;;
    esac
  fi
  echo "$resolved"
}

require_bound_managed_path() {
  local user="$1" root="$2" path="$3"
  local normalized_root normalized target target_relative root_relative
  require_linux_user "$user"
  case "$root" in
    *$'\n'*) deny "unsafe root: $root" ;;
    "") deny "empty root" ;;
    "..") deny "root traversal not allowed" ;;
    "../"*|*"/.."|*"/../"*) deny "root traversal not allowed" ;;
  esac
  [[ "$root" == /* ]] || deny "root must be absolute: $root"
  normalized_root=$(python3 -c 'import os, sys; print(os.path.normpath(sys.argv[1]))' "$root") || deny "cannot normalize $root"
  case "$normalized_root/" in
    "$HOME_ROOT/$user/"*) ;;
    *) deny "root is not owned by panel Linux user $user: $normalized_root" ;;
  esac
  root_relative="${normalized_root#${HOME_ROOT}/${user}/}"
  require_site_domain_segment "${root_relative%%/*}"
  [[ "$root_relative" == */* ]] && deny "site root must be a direct domain path: $normalized_root"

  case "$path" in
    *$'\n'*) deny "unsafe path: $path" ;;
    "") deny "empty path" ;;
    "..") deny "path traversal not allowed" ;;
    "../"*|*"/.."|*"/../"*) deny "path traversal not allowed" ;;
  esac
  [[ "$path" == /* ]] || deny "path must be absolute: $path"
  normalized=$(python3 -c 'import os, sys; print(os.path.normpath(sys.argv[1]))' "$path") || deny "cannot normalize $path"
  case "$normalized/" in
    "$normalized_root"|"$normalized_root/"*) ;;
    *) deny "path outside expected site root: $normalized" ;;
  esac
  target="$normalized"
  target_relative="${target#${HOME_ROOT}/${user}/}"
  [[ "$target" == "$normalized_root" || "$target_relative" == */* ]] || deny "refusing to operate on a panel user home"
  echo "$target"
}

delete_no_follow() {
  local user="$1" root="$2" target="$3"
  python3 - "$user" "$root" "$target" <<'PY'
import os
import stat
import sys

user, root, target = sys.argv[1:4]
base = f"/home/{user}"
root = os.path.normpath(root)
target = os.path.normpath(target)

if os.path.dirname(root) != base:
    raise SystemExit("invalid site root")
if target != root and not target.startswith(root + os.sep):
    raise SystemExit("target outside site root")

rel = os.path.relpath(target, base)
if rel.startswith("..") or rel == ".":
    raise SystemExit("target outside site root")

def open_child(parent_fd, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)

base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    parent_fd = base_fd
    close_parent = False
    parts = rel.split(os.sep)
    for part in parts[:-1]:
        next_fd = open_child(parent_fd, part)
        if close_parent:
            os.close(parent_fd)
        parent_fd = next_fd
        close_parent = True

    leaf = parts[-1]

    def remove_entry(dir_fd, name):
        st = os.lstat(name, dir_fd=dir_fd)
        if stat.S_ISDIR(st.st_mode):
            child_fd = open_child(dir_fd, name)
            try:
                for entry in os.listdir(child_fd):
                    remove_entry(child_fd, entry)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)

    remove_entry(parent_fd, leaf)
finally:
    try:
        if 'parent_fd' in locals() and parent_fd != base_fd:
            os.close(parent_fd)
    finally:
        os.close(base_fd)
PY
}

require_terminal_cwd() {
  local path="$1" user="$2" resolved
  require_linux_user "$user"
  resolved=$(require_safe_path "$HOME_ROOT" "$path")
  case "$resolved" in
    "$HOME_ROOT/$user"|"$HOME_ROOT/$user"/*) ;;
    *) deny "terminal cwd is not owned by panel Linux user $user: $resolved" ;;
  esac
  [[ -d "$resolved" ]] || deny "terminal cwd is not a directory: $resolved"
  echo "$resolved"
}

# Options of the allowlisted file commands that run another program. The
# hyphen skip in require_terminal_path_args means an option is never treated as
# a path, so these have to be refused by name or they slip past the containment
# check entirely -- `find . -exec sh -c '<cmd>' \;` was the shortest example.
TERMINAL_EXEC_OPTIONS=(
  # find / GNU findutils
  -exec -execdir -ok -okdir -fprint -fprint0 -fprintf -fls
  # tar
  --to-command --use-compress-program --checkpoint-action --rmt-command -I
  # zip / unzip
  -TT --unzip-command
  # grep
  --devices=read
)

require_terminal_safe_options() {
  local arg banned
  for arg in "$@"; do
    for banned in "${TERMINAL_EXEC_OPTIONS[@]}"; do
      if [[ "$arg" == "$banned" || "$arg" == "$banned="* ]]; then
        deny "option $banned runs another program and is not allowed in the terminal"
      fi
    done
  done
}

require_terminal_path_args() {
  local user="$1" cwd="$2" arg resolved
  shift 2
  require_linux_user "$user"
  require_terminal_safe_options "$@"
  for arg in "$@"; do
    case "$arg" in
      ""|"-"*|"--") continue ;;
      *$'\n'*|".."|"../"*|*"/.."|*"/../"*) deny "terminal path argument escapes user home: $arg" ;;
    esac
    if [[ "$arg" = /* ]]; then
      resolved=$(readlink -m -- "$arg") || deny "cannot resolve terminal path: $arg"
    else
      resolved=$(readlink -m -- "$cwd/$arg") || deny "cannot resolve terminal path: $arg"
    fi
    case "$resolved/" in
      "$HOME_ROOT/$user/"*) ;;
      *) deny "terminal path argument is outside panel user home: $arg" ;;
    esac
  done
}

_block_internal_url() {
  local url="$1" host port resolved
  host="${url#*://}"
  host="${host%%/*}"
  host="${host%%\?*}"
  port="${host##*:}"
  if [[ "$port" == "$host" ]]; then port=""; fi
  host="${host%%:*}"
  host="${host#[@]}"
  [[ -z "$host" ]] && return
  case "$host" in
    169.254.*|metadata.google.internal)
      deny "terminal URL points to a cloud metadata endpoint: $url"
      ;;
    0.0.0.0|[::])
      deny "terminal URL points to an unspecified address: $url"
      ;;
  esac
  if command -v getent >/dev/null 2>&1; then
    while IFS= read -r resolved; do
      case "$resolved" in
        169.254.*|fe80:*|fc00:*|fd00:*) deny "terminal URL resolves to a link-local or private address: $url" ;;
      esac
    done < <(getent ahosts "$host" 2>/dev/null | awk '{print $1}' | sort -u)
  fi
}

require_terminal_download_args() {
  local user="$1" cwd="$2" arg value expect_output=0
  shift 2
  for arg in "$@"; do
    case "${arg,,}" in
      file://*) deny "terminal URL argument uses local file scheme: $arg" ;;
    esac
    if (( expect_output )); then
      require_terminal_path_args "$user" "$cwd" "$arg"
      expect_output=0
      continue
    fi
    case "$arg" in
      --output=*|--output-document=*|-O=*)
        value="${arg#*=}"
        require_terminal_path_args "$user" "$cwd" "$value"
        ;;
      -o|-O|--output|--output-document)
        expect_output=1
        ;;
      http://*|https://*|ftp://*|ftps://*|sftp://*)
        _block_internal_url "$arg"
        ;;
      -*|"")
        ;;
      *)
        require_terminal_path_args "$user" "$cwd" "$arg"
        ;;
    esac
  done
  (( expect_output == 0 )) || deny "terminal download output path is missing"
}

ensure_sites_group() {
  getent group "$opanel_SITES_GROUP" >/dev/null || groupadd --system "$opanel_SITES_GROUP"
  usermod -aG "$opanel_SITES_GROUP" opanel 2>/dev/null || true
  usermod -aG "$opanel_SITES_GROUP" www-data 2>/dev/null || true
  ensure_site_log_rotation
}

# Log rotation for everything OpenLiteSpeed and PHP write. The first version of
# this covered only */php_error.log, weekly, with delaycompress -- so one site
# in an error loop reached 36 GB in a single uncompressed rotation, the
# per-site access logs were never rotated at all, and stderr.log was left
# entirely to OpenLiteSpeed, which rolls it by date and then never deletes it
# (a 25 GB archive from three weeks earlier was still on disk).
#
# Daily, seven days, uncompressed: logs stay greppable without zcat, which is
# the point of keeping them. maxsize is the safety valve -- a site writing
# gigabytes a day rotates before the daily run rather than after it.
LOG_ROTATION_VERSION=3
ensure_site_log_rotation() {
  command -v logrotate >/dev/null 2>&1 || return 0
  if [[ -f /etc/logrotate.d/opanel-sites ]] \
     && grep -q "opanel log rotation v${LOG_ROTATION_VERSION}" /etc/logrotate.d/opanel-sites; then
    return 0
  fi
  cat >/etc/logrotate.d/opanel-sites <<EOF
# opanel log rotation v${LOG_ROTATION_VERSION} -- managed by opanel, edits are overwritten
${PHP_LOG_ROOT}/*/php_error.log
/var/log/openlitespeed/*/php_error.log
/var/log/openlitespeed/*.access.log
/var/log/openlitespeed/*.error.log {
    daily
    rotate 7
    maxsize 2G
    missingok
    notifempty
    nocompress
    copytruncate
}

/usr/local/lsws/logs/error.log
/usr/local/lsws/logs/stderr.log {
    daily
    rotate 7
    maxsize 2G
    missingok
    notifempty
    nocompress
    copytruncate
    postrotate
        # OpenLiteSpeed rolls these itself into date-stamped archives and never
        # removes them. Same seven days.
        find /usr/local/lsws/logs -maxdepth 1 -type f \\
             -regextype posix-extended -regex '.*\\.log\\.[0-9]{4}_[0-9]{2}_[0-9]{2}(\\.[0-9]+)?' \\
             -mtime +7 -delete 2>/dev/null || true
    endscript
}
EOF
  chmod 0644 /etc/logrotate.d/opanel-sites
}

# OpenLiteSpeed ships logLevel DEBUG in the server error log block. opanel sets
# WARN on every vhost it writes but never touched the server block, so every
# install has been writing debug output since the day it was built.
ensure_ols_server_log_level() {
  local conf=/usr/local/lsws/conf/httpd_config.conf
  [[ -f "$conf" ]] || return 0
  grep -qE '^[[:space:]]*logLevel[[:space:]]+DEBUG[[:space:]]*$' "$conf" || return 0
  cp -a "$conf" "${conf}.bak.loglevel"
  sed -i -E 's/^([[:space:]]*)logLevel([[:space:]]+)DEBUG[[:space:]]*$/\1logLevel\2WARN/' "$conf"
  echo "OpenLiteSpeed server logLevel: DEBUG -> WARN"
}

# OpenLiteSpeed's stock expiresByType names application/javascript and
# application/x-javascript, but its own mime.properties serves .js as
# text/javascript. So CSS and images got a week of browser cache and no
# JavaScript file on any site got any: every page view revalidated every
# script. Rides on log-hygiene because that runs on every update.
ensure_ols_js_expires() {
  local conf=/usr/local/lsws/conf/httpd_config.conf
  [[ -f "$conf" ]] || return 0
  grep -qE '^[[:space:]]*expiresByType[[:space:]]' "$conf" || return 0
  grep -qE '^[[:space:]]*expiresByType[[:space:]].*text/javascript=' "$conf" && return 0
  cp -a "$conf" "${conf}.bak.jsexpires"
  sed -i -E '/^[[:space:]]*expiresByType[[:space:]]/{/text\/javascript=/!s/[[:space:]]*$/,text\/javascript=A604800/}' "$conf"
  restart_openlitespeed 2>/dev/null || true
  echo "OpenLiteSpeed: .js files now get the same browser cache lifetime as CSS"
}

# 99-opanel.ini is only written when a PHP version is installed, so an existing
# box never receives a change to it. The panel's own PHP tuning also writes
# opcache.jit into this file, which is where the value being fixed came from.
ensure_php_jit_disabled() {
  local ini changed=0
  for ini in /usr/local/lsws/lsphp*/etc/php/*/mods-available/99-opanel.ini; do
    [[ -f "$ini" ]] || continue
    grep -qE '^[[:space:]]*opcache\.jit[[:space:]]*=[[:space:]]*disable' "$ini" && continue
    if grep -qE '^[[:space:]]*opcache\.jit[[:space:]]*=' "$ini"; then
      sed -i -E 's/^([[:space:]]*opcache\.jit[[:space:]]*=).*/\1 disable/' "$ini"
      sed -i -E 's/^([[:space:]]*opcache\.jit_buffer_size[[:space:]]*=).*/\1 0/' "$ini"
    else
      printf '\nopcache.jit = disable\nopcache.jit_buffer_size = 0\n' >>"$ini"
    fi
    changed=1
  done
  if (( changed )); then
    restart_openlitespeed 2>/dev/null || true
    echo "PHP JIT disabled (ionCube turns it off anyway and warns on every worker start)"
  fi
}

# journald defaults to 10% of the filesystem, which is 50 GB on a 500 GB disk.
ensure_ols_defaults_private() {
  # OpenLiteSpeed ships a demo site ("Example", with phpinfo.php and an
  # upload.php that writes into /tmp) on *:8088 and its WebAdmin console on
  # *:7080. OPanel uses neither, and with the panel firewall off -- its default
  # -- both answered from the internet on a fresh install. The demo site goes;
  # WebAdmin listens on 127.0.0.1 (an SSH tunnel reaches it if ever needed).
  local changed
  [[ -f "$OLS_HTTPD_CONF" ]] || return 0
  changed="$(python3 - "$OLS_HTTPD_CONF" /usr/local/lsws/admin/conf/admin_config.conf <<'PY'
import pathlib
import re
import sys

changed = []
conf = pathlib.Path(sys.argv[1])
text = conf.read_text(encoding="utf-8")
# OLS writes it "virtualHost Example{" -- any case, any spacing.
new = re.sub(r"(?msi)^virtualhost[ \t]+Example[ \t]*\{.*?^\}[ \t]*\n?", "", text)


def listener(match):
    block = match.group(0)
    block = re.sub(r"(?m)^([ \t]*address[ \t]+)\*:8088[ \t]*$", r"\g<1>127.0.0.1:8088", block)
    return re.sub(r"(?m)^[ \t]*map[ \t]+Example[ \t]+\*[ \t]*\n", "", block)


new = re.sub(r"(?msi)^listener[ \t]+Default[ \t]*\{.*?^\}", listener, new)
if new != text:
    conf.write_text(new, encoding="utf-8")
    changed.append("demo site")
admin = pathlib.Path(sys.argv[2])
if admin.is_file():
    current = admin.read_text(encoding="utf-8")
    private = re.sub(r"(?m)^([ \t]*address[ \t]+)\*:7080[ \t]*$", r"\g<1>127.0.0.1:7080", current)
    if private != current:
        admin.write_text(private, encoding="utf-8")
        changed.append("WebAdmin")
print(", ".join(changed))
PY
)" || return 0
  if [[ -n "$changed" ]]; then
    restart_openlitespeed >/dev/null 2>&1 || true
    echo "OpenLiteSpeed: closed to the internet: ${changed}"
  fi
}

# Units a hosting VPS never uses, masked by host-trim at install time. Each
# was measured on a fresh Ubuntu 24.04 VPS (2 GB, 2026-09-30): multipathd
# 22 MB resident, fwupd 33 MB once apt starts it, ModemManager 7 MB, udisks2
# 6 MB, upower 3 MB.
HOST_TRIM_UNITS=(ModemManager.service udisks2.service upower.service fwupd.service fwupd-refresh.service fwupd-refresh.timer)

host_trim() {
  local record="${opanel_DATA_DIR}/host-trim.txt" unit pkg removed=() purge=() simulated
  install -d -m 0755 "$opanel_DATA_DIR"
  touch "$record"
  _trim_unit() {
    systemctl list-unit-files "$1" --no-legend 2>/dev/null | grep -q . || return 0
    [[ "$(systemctl is-enabled "$1" 2>/dev/null)" == "masked" ]] && return 0
    systemctl disable --now "$1" >/dev/null 2>&1 || true
    systemctl mask "$1" >/dev/null 2>&1 || return 0
    grep -qx "$1" "$record" || echo "$1" >>"$record"
    removed+=("$1")
  }
  for unit in "${HOST_TRIM_UNITS[@]}"; do
    _trim_unit "$unit"
  done
  # multipath only matters for SAN storage with several paths to one disk.
  if command -v multipath >/dev/null 2>&1 && ! multipath -l 2>/dev/null | grep -q .; then
    _trim_unit multipathd.service
    _trim_unit multipathd.socket
  fi

  # phpMyAdmin needs a provider of php-json, and apt picks libapache2-mod-php,
  # which brings Apache; OpenLiteSpeed serves phpMyAdmin, so Apache never runs.
  # Purged only when apt confirms phpMyAdmin stays (php-cli provides php-json)
  # -- ssl-cert stays: its snakeoil certificate serves the tools port.
  if dpkg-query -W -f='${Status}' apache2 2>/dev/null | grep -q "install ok installed" \
      && ! systemctl is-active --quiet apache2 2>/dev/null; then
    for pkg in apache2 apache2-bin apache2-data apache2-utils $(dpkg-query -W 'libapache2-mod-php*' 2>/dev/null | awk '{print $1}'); do
      dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" && purge+=("$pkg")
    done
    simulated="$(apt-get -s purge "${purge[@]}" 2>/dev/null | awk '/^(Purg|Remv) /{print $2}')" || simulated="phpmyadmin"
    if ! grep -qx "phpmyadmin" <<<"$simulated"; then
      apt-mark manual ssl-cert >/dev/null 2>&1 || true
      if DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 purge -y "${purge[@]}" >/dev/null 2>&1; then
        removed+=("apache2 (${#purge[@]} packages)")
        grep -qx "apache2 purged" "$record" || echo "apache2 purged" >>"$record"
      fi
    fi
  fi
  if (( ${#removed[@]} )); then
    echo "Host trimmed: ${removed[*]} (undo a unit with: systemctl unmask <unit>)"
  else
    echo "Host trim: nothing to do"
  fi
}

ensure_journal_cap() {
  local conf=/etc/systemd/journald.conf
  [[ -f "$conf" ]] || return 0
  grep -qE '^[[:space:]]*SystemMaxUse=' "$conf" && return 0
  printf '\n# Managed by opanel: journald otherwise grows to 10%% of the disk.\nSystemMaxUse=1G\n' >>"$conf"
  systemctl restart systemd-journald 2>/dev/null || true
  journalctl --vacuum-size=1G >/dev/null 2>&1 || true
  echo "journald capped at 1G"
}

ensure_sftp_group() {
  getent group "$opanel_SFTP_GROUP" >/dev/null || groupadd --system "$opanel_SFTP_GROUP"
}

clear_path_acl() {
  local target="$1"
  if command -v setfacl >/dev/null 2>&1; then
    setfacl -b -k "$target" 2>/dev/null || true
  fi
}

harden_site_dir() {
  local target="$1" user="$2"
  chown "$user:$user" "$target"
  clear_path_acl "$target"
  chmod 0755 "$target"
  chmod a-s "$target" 2>/dev/null || true
  chmod -t "$target" 2>/dev/null || true
}

harden_site_file() {
  local target="$1" user="$2"
  chown "$user:$user" "$target"
  clear_path_acl "$target"
  chmod 0644 "$target"
  chmod a-s "$target" 2>/dev/null || true
  chmod -t "$target" 2>/dev/null || true
}

harden_site_dir_path() {
  local root="$1" target="$2" user="$3"
  ensure_sites_group
  require_linux_user "$user"
  root=$(readlink -m "$root") || deny "cannot resolve $root"
  target=$(readlink -m "$target") || deny "cannot resolve $target"
  case "$target" in
    "$root"|"$root"/*) ;;
    *) deny "directory path outside site root: $target" ;;
  esac
  [[ -d "$target" ]] || deny "site directory does not exist: $target"
  # Apply ownership and mode on descriptors opened O_NOFOLLOW, never by
  # re-resolving the path. require_safe_path resolves once with `readlink -m`
  # and returns a string; the old implementation then re-derived each component
  # by name and called chown/chmod on it, both of which follow symlinks, inside
  # directories the site user owns. Replacing a component between the resolve
  # and the walk made root chown an arbitrary directory to that user, and the
  # trailing `[[ ! -L "$target" ]]` guards could not catch it because $target
  # was already fully dereferenced. rm-site has used this shape all along via
  # delete_no_follow; the hardening path had not.
  python3 - "$user" "$root" "$target" <<'HARDENPY'
import os
import pwd
import sys

user, root, target = sys.argv[1:4]
base = f"/home/{user}"
root = os.path.normpath(root)
target = os.path.normpath(target)

if os.path.dirname(root) != base:
    raise SystemExit("invalid site root")
if target != root and not target.startswith(root + os.sep):
    raise SystemExit("target outside site root")

rel = os.path.relpath(target, base)
if rel.startswith("..") or rel == ".":
    raise SystemExit("target outside site root")

entry = pwd.getpwnam(user)
uid, gid = entry.pw_uid, entry.pw_gid

base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
open_fds = [base_fd]
try:
    parent_fd = base_fd
    for part in rel.split(os.sep):
        try:
            child_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
            )
        except OSError as exc:
            raise SystemExit(f"refusing to harden {part!r}: {exc}")
        open_fds.append(child_fd)
        os.fchown(child_fd, uid, gid)
        # 0755 matches fix_site_tree. The cross-tenant boundary is the 0750
        # home directory above these, not this mode.
        os.fchmod(child_fd, 0o755)
        parent_fd = child_fd
finally:
    for fd in open_fds:
        try:
            os.close(fd)
        except OSError:
            pass
HARDENPY
  # No status check here on purpose: this script runs under `set -euo pipefail`,
  # so a non-zero exit from python3 aborts the helper immediately with the
  # SystemExit message python printed -- the line that used to follow could
  # never have run. delete_no_follow relies on the same behaviour.
}

ensure_panel_user_home() {
  local user="$1" home_dir="$HOME_ROOT/$1"
  ensure_sites_group
  ensure_sftp_group
  require_linux_user "$user"
  getent group "$user" >/dev/null || groupadd "$user"
  usermod -aG "$user" www-data 2>/dev/null || true
  # The panel reads site trees directly as the opanel account -- the file
  # manager (file_manager.read_text_file, list_directory) and the backup
  # writer (backup._add_tree) walk them in-process rather than through this
  # helper. Group membership is what keeps that working now that the home
  # directory no longer grants access to every local uid via its "other" bits.
  # This grants opanel nothing new: it already reaches root through the
  # unrestricted sudo grant on this script.
  usermod -aG "$user" opanel 2>/dev/null || true
  chown root:root "$HOME_ROOT"
  chmod 0711 "$HOME_ROOT"
  chmod a-s "$HOME_ROOT" 2>/dev/null || true
  chmod -t "$HOME_ROOT" 2>/dev/null || true
  if ! id -u "$user" >/dev/null 2>&1; then
    useradd --create-home --home-dir "$home_dir" --shell /usr/sbin/nologin --gid "$user" "$user"
  fi
  usermod --home "$home_dir" --shell /usr/sbin/nologin --gid "$user" "$user" 2>/dev/null || true
  usermod -aG "$opanel_SFTP_GROUP" "$user" 2>/dev/null || true
  mkdir -p "$home_dir"
  chown "root:$user" "$home_dir"
  # 0750, not 0751. The "other" execute bit let any local uid traverse into
  # another tenant's home; combined with the 0755/0644 modes fix_site_tree
  # applies inside, one site's PHP or shell could read a neighbour's
  # wp-config.php and reach their database. This directory is the gate, so
  # tightening it holds even for files PHP later creates at the process umask.
  # Still root-owned and not group-writable, which is what sshd requires of a
  # ChrootDirectory for the SFTP jail.
  chmod 0750 "$home_dir"
  chmod a-s "$home_dir" 2>/dev/null || true
  chmod -t "$home_dir" 2>/dev/null || true
  clear_path_acl "$home_dir"
  grant_panel_home_access "$home_dir"
}

# The usermod -aG above reaches a process only when it starts, so an
# opanel-api already running when this user was created -- by a DirectAdmin
# import, or from the Users page -- could not enter the new home until it was
# restarted: the Users page returned 500 on the storage walk, and the file
# manager and backups failed on the new sites. A named-user ACL is checked at
# access time. It grants the same r-x the group does and no write, so the
# directory still meets sshd's ChrootDirectory rules.
grant_panel_home_access() {
  command -v setfacl >/dev/null 2>&1 || return 0
  setfacl -m u:opanel:r-x "$1" 2>/dev/null || true
}

set_panel_user_password() {
  local user="$1" password
  require_linux_user "$user"
  id -u "$user" >/dev/null 2>&1 || deny "panel Linux user does not exist: $user"
  password="$(cat)"
  password="${password%$'\n'}"
  [[ ${#password} -ge 12 && ${#password} -le 72 ]] || deny "password must be 12-72 characters"
  case "$password" in
    *:*|*$'\r'*|*$'\n'*) deny "password cannot contain ':', carriage returns or newlines" ;;
  esac
  printf '%s:%s\n' "$user" "$password" | chpasswd
  passwd -u "$user" >/dev/null 2>&1 || true
}

delete_panel_user_runtime() {
  local user="$1"
  require_linux_user "$user"
  for dir in /usr/local/lsws/lsphp*/etc/php.d; do
    [[ -d "$dir" ]] || continue
    for pool_file in "$dir"/opanel-${user}.conf "$dir"/opanel-${user}-*.conf; do
      [[ -f "$pool_file" ]] || continue
      rm -f "$pool_file"
    done
  done
  restart_openlitespeed 2>/dev/null || true
  # Stale lsphp sockets and pid files, opanel-<user>-<site hash>-lsphpNN.sock.
  # Matched exactly rather than by opanel-<user>-*, which for a user "baba"
  # would also take "baba-x"'s sockets.
  local sock
  for sock in /tmp/lshttpd/opanel-"$user"-*; do
    [[ "$(basename -- "$sock")" =~ ^opanel-${user}-[0-9a-f]{12}-lsphp[0-9]+\.sock(\.pid)?$ ]] || continue
    rm -f -- "$sock"
  done
  crontab -r -u "$user" 2>/dev/null || true
  # Extra SFTP logins share this uid and group; they go first, or groupdel
  # below refuses (their primary group) and their jails keep a live mount.
  sftp_sub_delete_all_for_owner "$user"
  pkill -u "$user" 2>/dev/null || true
  userdel "$user" 2>/dev/null || true
  groupdel "$user" 2>/dev/null || true
  rm -rf "$HOME_ROOT/$user" 2>/dev/null || true
  rm -rf "/var/lib/php/sessions/$user" 2>/dev/null || true
  rm -rf "/var/lib/php/uploads/$user" 2>/dev/null || true
}

site_php_pool_glob() {
  local user="$1" target="$2"
  require_linux_user "$user"
  target=$(readlink -m "$target") || deny "cannot resolve $target"
  local site_hash
  site_hash="$(printf '%s' "$target" | sha256sum | awk '{print substr($1, 1, 12)}')"
  printf 'opanel-%s-%s-*' "$user" "$site_hash"
}

positive_int_or_default() {
  local value="${1:-}" default="$2" min="${3:-1}" max="${4:-}"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    value="$default"
  fi
  if (( value < min )); then
    value="$min"
  fi
  if [[ -n "$max" ]] && (( value > max )); then
    value="$max"
  fi
  printf '%s\n' "$value"
}

php_fpm_tuning_value() {
  local key="$1" default="$2" value=""
  if [[ -v "$key" ]]; then
    value="${!key}"
  fi
  if [[ -z "$value" ]]; then
    value="$(env_get "$key" 2>/dev/null || true)"
  fi
  printf '%s\n' "${value:-$default}"
}

php_fpm_total_memory_mb() {
  local total
  total="$(awk '/^MemTotal:/ { print int($2 / 1024); exit }' /proc/meminfo 2>/dev/null || true)"
  positive_int_or_default "$total" 1024 256 1048576
}

php_fpm_cpu_count() {
  local count
  count="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
  positive_int_or_default "$count" 1 1 256
}

php_fpm_pool_count() {
  local current_pool="${1:-}" count=0 pool_file
  shopt -s nullglob
  for pool_file in /usr/local/lsws/lsphp*/etc/php.d/opanel-*.conf; do
    [[ -f "$pool_file" ]] || continue
    count=$((count + 1))
  done
  shopt -u nullglob
  if [[ -n "$current_pool" && ! -f "$current_pool" ]]; then
    count=$((count + 1))
  fi
  if (( count < 1 )); then
    count=1
  fi
  printf '%s\n' "$count"
}

php_fpm_reserved_memory_mb() {
  local total="$1" reserve
  if (( total <= 1024 )); then
    reserve=$((total * 45 / 100))
    (( reserve >= 448 )) || reserve=448
  elif (( total <= 2048 )); then
    reserve=$((total * 35 / 100))
    (( reserve >= 640 )) || reserve=640
  elif (( total <= 4096 )); then
    reserve=$((total * 30 / 100))
    (( reserve >= 896 )) || reserve=896
  elif (( total <= 8192 )); then
    reserve=$((total * 25 / 100))
    (( reserve >= 1280 )) || reserve=1280
  else
    reserve=$((total * 20 / 100))
    (( reserve >= 2048 )) || reserve=2048
  fi
  if (( reserve > total - 128 )); then
    reserve=$((total - 128))
  fi
  if (( reserve < 128 )); then
    reserve=128
  fi
  printf '%s\n' "$reserve"
}

calculate_php_fpm_pool_tuning() {
  local current_pool="${1:-}" total_mb reserve_mb php_budget_mb cpu_count pool_count worker_mb
  local global_children pool_children cpu_cap profile_cap forced_children idle_default requests_default
  local active_pool_divisor pool_floor
  total_mb="$(php_fpm_total_memory_mb)"
  cpu_count="$(php_fpm_cpu_count)"
  pool_count="$(php_fpm_pool_count "$current_pool")"
  worker_mb="$(positive_int_or_default "$(php_fpm_tuning_value opanel_PHP_FPM_WORKER_MB "$LSPHP_DEFAULT_WORKER_MB")" "$LSPHP_DEFAULT_WORKER_MB" 32 1024)"
  reserve_mb="$(php_fpm_reserved_memory_mb "$total_mb")"
  php_budget_mb=$((total_mb - reserve_mb))
  if (( php_budget_mb < worker_mb )); then
    php_budget_mb="$worker_mb"
  fi

  global_children=$((php_budget_mb / worker_mb))
  (( global_children >= 1 )) || global_children=1

  active_pool_divisor=1
  while (( active_pool_divisor * active_pool_divisor < pool_count )); do
    active_pool_divisor=$((active_pool_divisor + 1))
  done
  pool_children=$((global_children / active_pool_divisor))
  (( pool_children >= 1 )) || pool_children=1

  cpu_cap=$((cpu_count * 4))
  if (( total_mb >= 3072 )); then
    cpu_cap=$((cpu_count * 6))
  fi
  if (( total_mb >= 8192 )); then
    cpu_cap=$((cpu_count * 8))
  fi
  (( cpu_cap >= 2 )) || cpu_cap=2
  (( cpu_cap <= 96 )) || cpu_cap=96

  if (( total_mb <= 1024 )); then
    pool_floor=1
    profile_cap=4
    idle_default=10
    requests_default=300
  elif (( total_mb <= 2048 )); then
    pool_floor=2
    profile_cap=8
    idle_default=15
    requests_default=400
  elif (( total_mb <= 4096 )); then
    pool_floor=3
    profile_cap=14
    idle_default=20
    requests_default=500
  elif (( total_mb <= 8192 )); then
    pool_floor=4
    profile_cap=24
    idle_default=30
    requests_default=750
  else
    pool_floor=6
    profile_cap=48
    idle_default=45
    requests_default=1000
  fi

  (( pool_children >= pool_floor )) || pool_children="$pool_floor"
  (( pool_children <= cpu_cap )) || pool_children="$cpu_cap"
  (( pool_children <= profile_cap )) || pool_children="$profile_cap"
  forced_children="$(php_fpm_tuning_value opanel_PHP_FPM_MAX_CHILDREN "")"
  if [[ -n "$forced_children" ]]; then
    pool_children="$(positive_int_or_default "$forced_children" "$pool_children" 1 512)"
  fi

  PHP_FPM_PM_MODE="ondemand"
  PHP_FPM_MAX_CHILDREN="$pool_children"
  PHP_FPM_PROCESS_IDLE_TIMEOUT="$(positive_int_or_default "$(php_fpm_tuning_value opanel_PHP_FPM_IDLE_TIMEOUT "$idle_default")" "$idle_default" 5 300)"
  PHP_FPM_MAX_REQUESTS="$(positive_int_or_default "$(php_fpm_tuning_value opanel_PHP_FPM_MAX_REQUESTS "$requests_default")" "$requests_default" 50 10000)"
  PHP_FPM_REQUEST_TERMINATE_TIMEOUT="$(positive_int_or_default "$(php_fpm_tuning_value opanel_PHP_FPM_REQUEST_TERMINATE_TIMEOUT "$LSPHP_DEFAULT_REQUEST_TERMINATE_TIMEOUT")" "$LSPHP_DEFAULT_REQUEST_TERMINATE_TIMEOUT" 30 3600)"
}

php_fpm_set_directive() {
  local file="$1" key="$2" value="$3" key_re
  key_re="${key//./\\.}"
  if grep -Eq "^[;[:space:]]*${key_re}[[:space:]]*=" "$file"; then
    sed -i -E "s|^[;[:space:]]*${key_re}[[:space:]]*=.*|${key} = ${value}|" "$file"
  else
    printf '%s = %s\n' "$key" "$value" >>"$file"
  fi
}

apply_php_fpm_tuning_to_pool_file() {
  local pool_file="$1"
  php_fpm_set_directive "$pool_file" "pm" "$PHP_FPM_PM_MODE"
  php_fpm_set_directive "$pool_file" "pm.max_children" "$PHP_FPM_MAX_CHILDREN"
  php_fpm_set_directive "$pool_file" "pm.process_idle_timeout" "${PHP_FPM_PROCESS_IDLE_TIMEOUT}s"
  php_fpm_set_directive "$pool_file" "pm.max_requests" "$PHP_FPM_MAX_REQUESTS"
  php_fpm_set_directive "$pool_file" "request_terminate_timeout" "${PHP_FPM_REQUEST_TERMINATE_TIMEOUT}s"
}

retune_php_fpm_pools() {
  local pool_file php_version pool_user count=0
  shopt -s nullglob
  for pool_file in /usr/local/lsws/lsphp*/etc/php.d/opanel-*.conf; do
    [[ -f "$pool_file" ]] || continue
    calculate_php_fpm_pool_tuning "$pool_file"
    apply_php_fpm_tuning_to_pool_file "$pool_file"
    pool_user="$(awk -F= '/^[[:space:]]*user[[:space:]]*=/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2; exit }' "$pool_file")"
    if [[ -n "$pool_user" ]]; then
      ensure_php_runtime_dirs "$pool_user"
      usermod -aG "$pool_user" www-data 2>/dev/null || true
    fi
    count=$((count + 1))
  done
  shopt -u nullglob
  restart_openlitespeed 2>/dev/null || true
  echo "Retuned ${count} opanel PHP-FPM pool(s)."
}

mariadb_tuning_value() {
  local key="$1" default="$2" value=""
  if [[ -v "$key" ]]; then
    value="${!key}"
  fi
  if [[ -z "$value" ]]; then
    value="$(env_get "$key" 2>/dev/null || true)"
  fi
  printf '%s\n' "${value:-$default}"
}

mariadb_megabytes() {
  local value="${1:-}" default="$2" number unit
  if [[ "$value" =~ ^([0-9]+)([KkMmGg]?)$ ]]; then
    number="${BASH_REMATCH[1]}"
    unit="${BASH_REMATCH[2]}"
    case "$unit" in
      [Kk]) printf '%s\n' $(((number + 1023) / 1024)) ;;
      [Gg]) printf '%s\n' $((number * 1024)) ;;
      *) printf '%s\n' "$number" ;;
    esac
    return 0
  fi
  printf '%s\n' "$default"
}

calculate_mariadb_tuning() {
  local total_mb cpu_count buffer_default buffer_mb log_file_mb tmp_mb max_connections thread_cache
  local table_open_cache open_files_limit packet_mb io_capacity
  total_mb="$(php_fpm_total_memory_mb)"
  cpu_count="$(php_fpm_cpu_count)"

  if (( total_mb <= 1024 )); then
    buffer_default=$((total_mb * 22 / 100))
    max_connections=35
    thread_cache=16
    table_open_cache=512
    tmp_mb=32
    packet_mb=64
  elif (( total_mb <= 2048 )); then
    buffer_default=$((total_mb * 25 / 100))
    max_connections=50
    thread_cache=24
    table_open_cache=512
    tmp_mb=48
    packet_mb=64
  elif (( total_mb <= 4096 )); then
    buffer_default=$((total_mb * 28 / 100))
    max_connections=80
    thread_cache=32
    table_open_cache=1024
    tmp_mb=64
    packet_mb=96
  elif (( total_mb <= 8192 )); then
    buffer_default=$((total_mb * 32 / 100))
    max_connections=120
    thread_cache=48
    table_open_cache=1024
    tmp_mb=96
    packet_mb=128
  else
    buffer_default=$((total_mb * 36 / 100))
    max_connections=180
    thread_cache=64
    table_open_cache=2048
    tmp_mb=128
    packet_mb=128
  fi

  (( buffer_default >= 128 )) || buffer_default=128
  (( buffer_default <= total_mb * 45 / 100 )) || buffer_default=$((total_mb * 45 / 100))
  buffer_mb="$(mariadb_megabytes "$(mariadb_tuning_value opanel_MARIADB_BUFFER_POOL_SIZE "${buffer_default}M")" "$buffer_default")"
  buffer_mb="$(positive_int_or_default "$buffer_mb" "$buffer_default" 128 "$((total_mb * 60 / 100))")"

  max_connections="$(positive_int_or_default "$(mariadb_tuning_value opanel_MARIADB_MAX_CONNECTIONS "$max_connections")" "$max_connections" 20 1000)"
  thread_cache="$(positive_int_or_default "$(mariadb_tuning_value opanel_MARIADB_THREAD_CACHE_SIZE "$thread_cache")" "$thread_cache" 8 256)"
  table_open_cache="$(positive_int_or_default "$(mariadb_tuning_value opanel_MARIADB_TABLE_OPEN_CACHE "$table_open_cache")" "$table_open_cache" 256 65535)"
  tmp_mb="$(mariadb_megabytes "$(mariadb_tuning_value opanel_MARIADB_TMP_TABLE_SIZE "${tmp_mb}M")" "$tmp_mb")"
  tmp_mb="$(positive_int_or_default "$tmp_mb" 64 16 512)"
  packet_mb="$(mariadb_megabytes "$(mariadb_tuning_value opanel_MARIADB_MAX_ALLOWED_PACKET "${packet_mb}M")" "$packet_mb")"
  packet_mb="$(positive_int_or_default "$packet_mb" 64 16 512)"
  log_file_mb=$((buffer_mb / 4))
  log_file_mb="$(positive_int_or_default "$(mariadb_megabytes "$(mariadb_tuning_value opanel_MARIADB_LOG_FILE_SIZE "${log_file_mb}M")" "$log_file_mb")" "$log_file_mb" 64 1024)"
  io_capacity=$((cpu_count * 200))
  io_capacity="$(positive_int_or_default "$(mariadb_tuning_value opanel_MARIADB_IO_CAPACITY "$io_capacity")" "$io_capacity" 200 4000)"
  open_files_limit=$((table_open_cache * 2 + max_connections + 512))
  open_files_limit="$(positive_int_or_default "$(mariadb_tuning_value opanel_MARIADB_OPEN_FILES_LIMIT "$open_files_limit")" "$open_files_limit" 2048 200000)"

  MARIADB_INNODB_BUFFER_POOL_SIZE="${buffer_mb}M"
  MARIADB_INNODB_LOG_FILE_SIZE="${log_file_mb}M"
  MARIADB_MAX_CONNECTIONS="$max_connections"
  MARIADB_THREAD_CACHE_SIZE="$thread_cache"
  MARIADB_TABLE_OPEN_CACHE="$table_open_cache"
  MARIADB_TMP_TABLE_SIZE="${tmp_mb}M"
  MARIADB_MAX_ALLOWED_PACKET="${packet_mb}M"
  MARIADB_INNODB_IO_CAPACITY="$io_capacity"
  MARIADB_OPEN_FILES_LIMIT="$open_files_limit"
}

write_mariadb_tuning() {
  calculate_mariadb_tuning
  install -d -o root -g root -m 0755 "$(dirname "$MARIADB_TUNING_CONF")"
  cat >"$MARIADB_TUNING_CONF" <<MYSQL
# OPanel auto-tunes MariaDB for small and medium VPS plans.
# Optional overrides in ${ENV_FILE}: opanel_MARIADB_BUFFER_POOL_SIZE,
# OPanel_MARIADB_MAX_CONNECTIONS, opanel_MARIADB_THREAD_CACHE_SIZE,
# OPanel_MARIADB_TABLE_OPEN_CACHE, opanel_MARIADB_TMP_TABLE_SIZE,
# OPanel_MARIADB_MAX_ALLOWED_PACKET, opanel_MARIADB_LOG_FILE_SIZE,
# OPanel_MARIADB_IO_CAPACITY, opanel_MARIADB_OPEN_FILES_LIMIT.
[mysqld]
innodb_buffer_pool_size = ${MARIADB_INNODB_BUFFER_POOL_SIZE}
innodb_log_file_size = ${MARIADB_INNODB_LOG_FILE_SIZE}
innodb_flush_log_at_trx_commit = 2
innodb_flush_method = O_DIRECT
innodb_io_capacity = ${MARIADB_INNODB_IO_CAPACITY}
max_connections = ${MARIADB_MAX_CONNECTIONS}
thread_cache_size = ${MARIADB_THREAD_CACHE_SIZE}
table_open_cache = ${MARIADB_TABLE_OPEN_CACHE}
tmp_table_size = ${MARIADB_TMP_TABLE_SIZE}
max_heap_table_size = ${MARIADB_TMP_TABLE_SIZE}
max_allowed_packet = ${MARIADB_MAX_ALLOWED_PACKET}
skip_name_resolve = 1
slow_query_log = 1
slow_query_log_file = /var/log/mysql/opanel-slow.log
long_query_time = 2

[server]
open_files_limit = ${MARIADB_OPEN_FILES_LIMIT}
MYSQL
}

ensure_mariadb_slow_log() {
  local log_dir="/var/log/mysql" log_file="/var/log/mysql/opanel-slow.log" log_group="mysql"
  getent group adm >/dev/null 2>&1 && log_group="adm"
  install -d -o mysql -g "$log_group" -m 0750 "$log_dir"
  touch "$log_file"
  chown mysql:"$log_group" "$log_file"
  chmod 0640 "$log_file"
}

retune_mariadb() {
  write_mariadb_tuning
  ensure_mariadb_slow_log
  mariadbd --help --verbose >/dev/null
  systemctl restart mariadb
  echo "Retuned MariaDB: innodb_buffer_pool_size=${MARIADB_INNODB_BUFFER_POOL_SIZE}, max_connections=${MARIADB_MAX_CONNECTIONS}, table_open_cache=${MARIADB_TABLE_OPEN_CACHE}."
}

delete_site_php_pools() {
  local user="$1" target="$2" glob
  glob="$(site_php_pool_glob "$user" "$target")"
  for dir in /etc/php/*/fpm/pool.d; do
    [[ -d "$dir" ]] || continue
    for pool_file in "$dir"/$glob.conf; do
      [[ -f "$pool_file" ]] || continue
      rm -f "$pool_file"
      local php_version
      php_version="$(echo "$dir" | awk -F/ '{print $4}')"
      systemctl reload "php${php_version}-fpm" 2>/dev/null || true
    done
  done
  for dir in /usr/local/lsws/lsphp*/etc/php.d; do
    [[ -d "$dir" ]] || continue
    for pool_file in "$dir"/$glob.conf; do
      [[ -f "$pool_file" ]] || continue
      rm -f "$pool_file"
    done
  done
  # The site's lsphp socket and pid file outlive its pool; the glob carries
  # the site hash, so it cannot reach another site of the same user.
  rm -f /tmp/lshttpd/$glob 2>/dev/null || true
  restart_openlitespeed 2>/dev/null || true
}

ensure_php_pool() {
  local user="$1" target="$2" php_version="$3"
  [[ "$php_version" != "none" ]] || return 0
  require_linux_user "$user"
  require_php_version "$php_version"
  target=$(readlink -m "$target") || deny "cannot resolve $target"
  local pool_suffix="lsphp${php_version//./}"
  local lsphp_version="${php_version//./}"
  local site_hash
  site_hash="$(printf '%s' "$target" | sha256sum | awk '{print substr($1, 1, 12)}')"
  local pool_name="opanel-${user}-${site_hash}-${pool_suffix}"
  local lsphp_dir="/usr/local/lsws/lsphp${lsphp_version}"
  [[ -x "${lsphp_dir}/bin/lsphp" ]] || deny "LSPHP ${php_version} is not installed"
  local pool_dir="${lsphp_dir}/etc/php.d"
  local pool_file="${pool_dir}/${pool_name}.conf"
  # Per-user dirs for sessions/uploads. Sharing /tmp across pools lets one
  # site read another's session files (mode 0600 helps but only inside the
  # same uid; uploads land world-writable on tmpfs). Using 0700 dirs owned
  # by the pool's Linux user contains the data inside the site's trust
  # boundary.
  local sess_dir="/var/lib/php/sessions/${user}"
  local upload_dir="/var/lib/php/uploads/${user}"
  ensure_php_runtime_dirs "$user"
  install -d -o root -g root -m 0755 "$pool_dir"
  calculate_php_fpm_pool_tuning "$pool_file"
  local pool_tmp
  pool_tmp="$(mktemp)"
  cat >"$pool_tmp" <<POOL
; opanel auto-tunes these values from RAM, CPU and managed pool count.
; Optional overrides: opanel_PHP_FPM_WORKER_MB, opanel_PHP_FPM_MAX_CHILDREN,
; opanel_PHP_FPM_IDLE_TIMEOUT, opanel_PHP_FPM_MAX_REQUESTS,
; opanel_PHP_FPM_REQUEST_TERMINATE_TIMEOUT.
; OLS starts this site as an LSAPI external app from the vhost config.
;
; NOTE: open_basedir, upload_tmp_dir and session.save_path used to be written
; here. They never took effect. This directory is not the build's ini scan
; directory (that is etc/php/<ver>/mods-available), and the scan directory only
; loads *.ini while these fragments are *.conf -- so every site ran with an
; empty open_basedir. They now live in each site's own vhost phpIniOverride
; block, which is scoped to one virtual host by construction; putting a
; per-site value in this build-shared directory could not have been correct
; even if it were read. See openlitespeed._php_open_basedir.
user = ${user}
group = ${user}
LSAPI_CHILDREN = ${PHP_FPM_MAX_CHILDREN}
LSAPI_MAX_IDLE = ${PHP_FPM_PROCESS_IDLE_TIMEOUT}
LSAPI_MAX_REQS = ${PHP_FPM_MAX_REQUESTS}
LSAPI_MAX_PROCESS_TIME = ${PHP_FPM_REQUEST_TERMINATE_TIMEOUT}
POOL
  # Skip the OLS restart when the pool config is byte-identical to what is
  # already deployed -- a bulk site refresh writes the same file back for every
  # site and would otherwise restart OLS once per site for no reason.
  if [[ -f "$pool_file" ]] && cmp -s "$pool_tmp" "$pool_file"; then
    rm -f "$pool_tmp"
    return 0
  fi
  install -m 0644 -o root -g root "$pool_tmp" "$pool_file"
  rm -f "$pool_tmp"
  restart_openlitespeed 2>/dev/null || true
}

ensure_php_runtime_dirs() {
  local user="$1"
  local sess_dir="/var/lib/php/sessions/${user}"
  local upload_dir="/var/lib/php/uploads/${user}"
  ensure_sites_group
  require_linux_user "$user"
  install -d -o www-data -g "$opanel_SITES_GROUP" -m 2775 /tmp/lshttpd
  chmod g+s /tmp/lshttpd 2>/dev/null || true
  # Assert the shared parents before creating anything under them. Nothing in
  # this repository used to create them, so their mode was whatever the distro
  # packages left: php-common ships /var/lib/php/sessions as 1733, i.e. sticky
  # but world-writable. The sticky bit stops a local uid removing another's
  # entry, not pre-creating a path component that a later root `install -d`
  # would resolve. `install -d` only applies -o/-g/-m to components it creates,
  # so an existing directory keeps its mode and has to be reset explicitly.
  install -d -o root -g root -m 0755 /var/lib/php
  install -d -o root -g root -m 0751 /var/lib/php/sessions
  install -d -o root -g root -m 0751 /var/lib/php/uploads
  chown root:root /var/lib/php /var/lib/php/sessions /var/lib/php/uploads
  chmod 0755 /var/lib/php
  chmod 0751 /var/lib/php/sessions /var/lib/php/uploads
  chmod -t /var/lib/php/sessions /var/lib/php/uploads 2>/dev/null || true
  install -d -o "$user" -g "$user" -m 0700 "$sess_dir"
  install -d -o "$user" -g "$user" -m 0700 "$upload_dir"
  chmod g-s "$upload_dir" 2>/dev/null || true
}

fix_site_tree() {
  local target="$1" user="$2"
  ensure_sites_group
  require_linux_user "$user"
  # chown -R is safe: GNU chown defaults to -P and does not follow symlinks.
  chown -R "$user:$user" "$target"
  if [[ -d "$target" ]]; then
    if command -v setfacl >/dev/null 2>&1; then
      setfacl -Rb "$target" 2>/dev/null || true
      find "$target" -type d -exec setfacl -k {} + 2>/dev/null || true
    fi
    # The mode pass walks on descriptors. `find -type d -exec chmod` selected
    # correctly -- find lstats, so it never matched a symlink -- but the
    # batched chmod re-resolved each path by name, and chmod always
    # dereferences. The site user owns these directories, so swapping an
    # enumerated entry for a symlink between the walk and the exec made root
    # chmod a path of their choosing. Same fix as harden_site_dir_path.
    python3 - "$target" <<'TREEPY'
import os
import stat
import sys

root = sys.argv[1]

DIR_MODE = 0o755
FILE_MODE = 0o644


def harden(dir_fd, name, is_dir):
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if is_dir:
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError:
        # A symlink, or the entry vanished. Either way it is not ours to chmod.
        return None
    try:
        info = os.fstat(fd)
        if is_dir and not stat.S_ISDIR(info.st_mode):
            os.close(fd)
            return None
        if not is_dir and not stat.S_ISREG(info.st_mode):
            os.close(fd)
            return None
        os.fchmod(fd, DIR_MODE if is_dir else FILE_MODE)
    except OSError:
        os.close(fd)
        return None
    if is_dir:
        return fd
    os.close(fd)
    return None


def walk(dir_fd):
    with os.scandir(dir_fd) as entries:
        children = list(entries)
    for entry in children:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
        except OSError:
            continue
        if not is_dir and not is_file:
            continue
        child_fd = harden(dir_fd, entry.name, is_dir)
        if child_fd is not None:
            try:
                walk(child_fd)
            finally:
                os.close(child_fd)


root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    os.fchmod(root_fd, DIR_MODE)
    walk(root_fd)
finally:
    os.close(root_fd)
TREEPY
    # Strip setgid/sticky separately: fchmod above already set the exact mode,
    # so these only matter for entries the walk skipped.
    find "$target" -type d -exec chmod a-s {} + 2>/dev/null || true
    find "$target" -type d -exec chmod -t {} + 2>/dev/null || true
  else
    harden_site_file "$target" "$user"
  fi
}

require_ip_or_cidr() {
  # Loose check; we trust iptables to do the final parsing.
  [[ "$1" =~ ^[0-9a-fA-F.:/]+$ ]] || deny "invalid IP/CIDR: $1"
}

cmd="${1:-}"
shift || true
audit_log "$@"

# --- Addons -----------------------------------------------------------------
#
# The panel may name an addon; it may never supply a command. This array is the
# helper's own copy of the registry in backend/app/services/addons.py, so a
# panel that has been talked into asking for something outside it gets nothing.
# Two lists that must agree is a real cost, and the alternative -- letting the
# caller say what to install -- is handing root to whoever can reach the API.
ADDON_IDS=("fail2ban" "mail" "dns")

require_addon_id() {
  local id="${1:-}" known
  [[ "$id" =~ ^[a-z][a-z0-9-]{1,31}$ ]] || deny "invalid addon id"
  for known in "${ADDON_IDS[@]}"; do
    if [[ "$id" == "$known" ]]; then
      return 0
    fi
  done
  deny "unknown addon: $id"
}

require_addon_ip() {
  # Loose on shape, strict on alphabet: fail2ban-client is the authority on
  # whether an address exists, but nothing that could reach a shell gets past
  # here.
  local value="${1:-}"
  [[ "$value" =~ ^[0-9a-fA-F.:]{3,49}$ ]] || deny "invalid address"
}

# --- Fail2ban ---------------------------------------------------------------
FAIL2BAN_JAIL_FILE="/etc/fail2ban/jail.d/opanel.local"
FAIL2BAN_FILTER_FILE="/etc/fail2ban/filter.d/opanel-panel.conf"
FAIL2BAN_SETTINGS_FILE="/var/lib/opanel/addons/fail2ban-settings.json"

addon_fail2ban_status() {
  local installed=0 running=0 enabled=0 version=""
  if command -v fail2ban-server >/dev/null 2>&1; then
    installed=1
    version="$(fail2ban-server --version 2>/dev/null \
      | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n 1 || true)"
  fi
  if systemctl is-active --quiet fail2ban 2>/dev/null; then running=1; fi
  if systemctl is-enabled --quiet fail2ban 2>/dev/null; then enabled=1; fi
  echo "installed=${installed} running=${running} enabled=${enabled} version=${version}"
}

addon_fail2ban_write_filter() {
  install -d -o root -g root -m 0755 /etc/fail2ban/filter.d
  # Matches the line app/api/auth.py logs on a failed sign-in. The address is
  # last and anchored, and the name has already been stripped to a safe
  # alphabet there, so <HOST> cannot be fed a forged candidate.
  cat >"$FAIL2BAN_FILTER_FILE" <<'FILTER'
# Managed by opanel -- edits are overwritten when the addon is reconfigured.
[Definition]
# The leading .* is deliberate. The journal carries the whole formatted record,
# so the line arrives with a level and logger prefix that uvicorn's formatter
# owns -- "WARNI [opanel.auth] " today, something else after a version bump.
# Anchoring at the start matched nothing at all, and a jail that reads nothing
# still reports itself as running.
#
# The prefix cannot be used to forge a match: the only attacker-controlled part
# of the line is the account name, and app/api/auth.py strips it to
# [A-Za-z0-9._@-], which cannot spell a space, a quote or a parenthesis.
failregex = ^.*opanel-auth: authentication failure \([^)]*\) for user '[^']*' from <HOST>\s*$
ignoreregex =
FILTER
  chmod 0644 "$FAIL2BAN_FILTER_FILE"
}

addon_fail2ban_write_jails() {
  # Reads the settings JSON and renders jail.d/opanel.local. Python does the
  # parsing so a value never passes through word splitting.
  local panel_port
  panel_port="$(env_get PANEL_PORT)"
  panel_port="${panel_port:-$DEFAULT_PANEL_PORT}"
  install -d -o root -g root -m 0755 /etc/fail2ban/jail.d
  python3 - "$FAIL2BAN_SETTINGS_FILE" "$FAIL2BAN_JAIL_FILE" "$panel_port" <<'PY'
import json
import re
import sys
from pathlib import Path

settings_path, jail_path, panel_port = sys.argv[1], sys.argv[2], sys.argv[3]

defaults = {
    "maxretry": 5,
    "bantime": 3600,
    "findtime": 600,
    "jail_sshd": True,
    "jail_panel": True,
    "ignoreip": "",
}
try:
    raw = json.loads(Path(settings_path).read_text(encoding="utf-8"))
except Exception:
    raw = {}
cfg = dict(defaults)
if isinstance(raw, dict):
    for key in defaults:
        if key in raw:
            cfg[key] = raw[key]


def whole(value, low, high, fallback):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if low <= number <= high else fallback


year = 365 * 24 * 3600
cfg["maxretry"] = whole(cfg["maxretry"], 1, 100, 5)
cfg["findtime"] = whole(cfg["findtime"], 10, year, 600)
bantime = -1 if str(cfg["bantime"]).strip() == "-1" else whole(cfg["bantime"], 10, year, 3600)

# Loopback is always exempt; a panel is operated from the box itself.
ignore = ["127.0.0.1/8", "::1"]
for token in str(cfg.get("ignoreip") or "").replace(",", " ").split():
    if re.fullmatch(r"[0-9a-fA-F:.]+(/\d{1,3})?", token) and len(token) <= 49:
        if token not in ignore:
            ignore.append(token)

port = panel_port if re.fullmatch(r"\d{1,5}", panel_port or "") else "2222"

lines = [
    "# Managed by opanel -- edited through the panel's Addons page.",
    "[DEFAULT]",
    "# The journal is where the panel's own log lines land, and sshd's too on a",
    "# systemd box; no log file has to exist for either jail to work.",
    "backend = systemd",
    f"bantime = {bantime}",
    f"findtime = {cfg['findtime']}",
    f"maxretry = {cfg['maxretry']}",
    "ignoreip = " + " ".join(ignore),
    "# A banned address is blocked on every port rather than only the one it",
    "# was caught on: a single chain per jail is also what lets the panel keep",
    "# these jumps above its own default port allowances.",
    "#",
    "# actionstart_on_demand is a property of the action, not a jail option, so",
    "# setting it in [DEFAULT] does nothing -- and iptables-allports ships",
    "# conditional sections, which makes fail2ban turn on-demand start on by",
    "# itself. Passed here it creates each chain when the jail starts. Left",
    "# on demand, a jail that has banned nobody yet has no chain for the panel",
    "# to position, and fail2ban later inserts one at the top of INPUT --",
    "# ahead of the admin rules that are supposed to outrank an automatic ban.",
    "action = iptables-allports[actionstart_on_demand=false]",
    "",
]

if bool(cfg["jail_sshd"]):
    lines += ["[sshd]", "enabled = true", ""]
else:
    lines += ["[sshd]", "enabled = false", ""]

if bool(cfg["jail_panel"]):
    lines += [
        "[opanel-panel]",
        "enabled = true",
        "filter = opanel-panel",
        f"port = {port}",
        "journalmatch = _SYSTEMD_UNIT=opanel-api.service",
        "",
    ]
else:
    lines += ["[opanel-panel]", "enabled = false", ""]

Path(jail_path).write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {jail_path}")
PY
  chmod 0644 "$FAIL2BAN_JAIL_FILE"
}

addon_fail2ban_install() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update --allow-releaseinfo-change >/dev/null 2>&1 || true
  apt-get -o DPkg::Lock::Timeout=300 install -y fail2ban || deny "apt-get install fail2ban failed"
  # Without python3-systemd the journal backend cannot read anything, and both
  # jails would sit enabled and blind.
  apt-get install -y python3-systemd >/dev/null 2>&1 || true
  install -d -o opanel -g opanel -m 0750 /var/lib/opanel/addons 2>/dev/null || \
    install -d -m 0750 /var/lib/opanel/addons
  # Debian ships a jail.conf that enables sshd with its own defaults. Ours is
  # in jail.d, which is read after it, so these values win.
  addon_fail2ban_write_filter
  addon_fail2ban_write_jails
  systemctl enable fail2ban >/dev/null 2>&1 || true
  systemctl restart fail2ban || deny "fail2ban failed to start -- check: journalctl -u fail2ban"
  addon_fail2ban_wait_for_chains
  iptables_restore_addon_precedence
  echo "fail2ban installed and started"
}

addon_fail2ban_remove_chains() {
  # A ban must not outlive the addon. Stopping the service normally makes
  # fail2ban undo its own rules, but if anything still references a f2b chain
  # the delete fails silently and the DROPs stay in force with no fail2ban
  # left to lift them. Take them out directly.
  local binary spec chain
  local -a parts=()
  for binary in iptables ip6tables; do
    while IFS= read -r spec; do
      if [[ -n "$spec" ]]; then
        read -ra parts <<<"$spec"
        "$binary" -D INPUT "${parts[@]:2}" 2>/dev/null || true
      fi
    done < <("$binary" -S INPUT 2>/dev/null | grep -E -- ' -j f2b-[A-Za-z0-9_.-]+$' || true)
    while IFS= read -r chain; do
      if [[ -n "$chain" ]]; then
        "$binary" -F "$chain" 2>/dev/null || true
        "$binary" -X "$chain" 2>/dev/null || true
      fi
    done < <("$binary" -S 2>/dev/null | awk '/^-N f2b-/ { print $2 }' || true)
  done
}

addon_fail2ban_uninstall() {
  export DEBIAN_FRONTEND=noninteractive
  # Stop first so fail2ban unwinds what it can while it still knows about it.
  systemctl disable --now fail2ban >/dev/null 2>&1 || true
  apt-get purge -y fail2ban >/dev/null 2>&1 || apt-get remove -y fail2ban >/dev/null 2>&1 || true
  rm -f "$FAIL2BAN_JAIL_FILE" "$FAIL2BAN_FILTER_FILE"
  addon_fail2ban_remove_chains
  echo "fail2ban removed"
}

addon_fail2ban_banned() {
  command -v fail2ban-client >/dev/null 2>&1 || deny "fail2ban is not installed"
  local jails jail addresses
  jails="$(fail2ban-client status 2>/dev/null \
    | sed -n 's/.*Jail list:[[:space:]]*//p' | tr ',' ' ' || true)"
  for jail in $jails; do
    [[ "$jail" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || continue
    addresses="$(fail2ban-client status "$jail" 2>/dev/null \
      | sed -n 's/.*Banned IP list:[[:space:]]*//p' || true)"
    echo "${jail}: ${addresses}"
  done
}

addon_fail2ban_log() {
  local count="${1:-40}"
  [[ "$count" =~ ^[0-9]{1,3}$ ]] || count=40
  if [[ -f /var/log/fail2ban.log ]]; then
    tail -n "$count" /var/log/fail2ban.log
  else
    journalctl -u fail2ban -n "$count" --no-pager 2>/dev/null || true
  fi
}

# --- Email: Exim + Dovecot + Rspamd + webmail --------------------------------
#
# The panel owns the mail data -- domains, mailboxes, forwarders -- in its
# database and hands the whole set to mail-sync as JSON on stdin. This section
# renders it into flat files that Exim and Dovecot read on every lookup, so a
# change needs no reload. Mailbox passwords arrive already hashed: the panel
# never gives root a password in clear, and nothing here puts one on a command
# line.
#
# Layout:
#   /etc/opanel-mail/          maps (root:Debian-exim or root:dovecot, 0640)
#   /etc/opanel-mail/dkim/     one private key per domain, selector "opanel"
#   /var/vmail/<domain>/<user> maildirs, all owned by the vmail user
#   /opt/bnix-webmail          the webmail, pinned to one commit, on
#                              127.0.0.1:18096 behind OpenLiteSpeed :2096
MAIL_DIR="/etc/opanel-mail"
MAIL_VMAIL_DIR="/var/vmail"
MAIL_SETTINGS_FILE="${MAIL_DIR}/settings.json"
MAIL_MARKER="${MAIL_DIR}/installed"
MAIL_WEBMAIL_HOSTS="${MAIL_DIR}/webmail-hosts"
MAIL_DKIM_SELECTOR="opanel"
MAIL_PACKAGES=(exim4-daemon-heavy dovecot-core dovecot-imapd dovecot-pop3d dovecot-lmtpd dovecot-sieve rspamd unbound)
# What removal may take with it. Anything else apt wants to remove is refused.
MAIL_REMOVABLE_PACKAGES=(exim4 exim4-base exim4-config exim4-daemon-heavy exim4-daemon-light
  dovecot-core dovecot-imapd dovecot-pop3d dovecot-lmtpd dovecot-sieve dovecot-managesieved rspamd unbound)
# 25, 465 and 587 are default allowances on every install already.
MAIL_EXTRA_PORTS=(110 143 993 995 2096)
MAIL_SERVICES=(unbound rspamd dovecot exim4 bnix-webmail)
MAIL_WEBMAIL_ROOT="/opt/bnix-webmail"
MAIL_WEBMAIL_ENV="/etc/bnix-webmail.env"
MAIL_WEBMAIL_UNIT="/etc/systemd/system/bnix-webmail.service"
MAIL_WEBMAIL_REPO="https://github.com/bnixvn/webmail.git"
# A commit, not a branch: what runs here is what was reviewed. The merge of
# single sign-on into the webmail's main (/api/auth/sso, with /sso kept for
# 1.25.0) and the UID SEARCH fix for aioimaplib 2.
MAIL_WEBMAIL_COMMIT="8b694ac30109003e3ea2d61058cfa4f660a23b18"
MAIL_WEBMAIL_PORT="18096"
MAIL_WEBMAIL_PUBLIC_PORT="2096"
MAIL_WEBMAIL_VHOST="${OLS_VHOSTS_DIR}/00-opanel-webmail.conf"
MAIL_WEBMAIL_DOCROOT="/var/www/opanel-webmail"
MAIL_SSO_SECRET_FILE="${opanel_DATA_DIR}/addons/mail-sso.secret"
# The Dovecot master user the webmail signs in as for an SSO session
# ("mailbox*opanel-sso"). Its passdb entry only accepts loopback.
MAIL_SSO_MASTER="opanel-sso"
# Rspamd's own recursive resolver. Spamhaus and the URI blocklists answer
# "blocked" to queries arriving from public resolvers (8.8.8.8, 1.1.1.1 --
# what a VPS usually has), which silently turns the DNS blocklists off. It
# listens on its own port so the system's resolver is left alone.
MAIL_UNBOUND_PORT="5335"
MAIL_UNBOUND_CONF="/etc/unbound/unbound.conf.d/opanel-mail.conf"

mail_installed() {
  [[ -f "$MAIL_MARKER" ]]
}

require_mail_installed() {
  mail_installed || deny "the Email addon is not installed"
}

require_mail_address() {
  local value="${1:-}"
  [[ ${#value} -le 254 && "$value" =~ ^[a-z0-9]([a-z0-9._+-]{0,62}[a-z0-9_+-])?@[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]] \
    || deny "invalid mailbox address: $value"
  [[ "$value" != *..* ]] || deny "invalid mailbox address: $value"
}

mail_hostname() {
  # The panel's name for the server; app/services/mail.py hostname() derives
  # the same one, so what customers are told to use is what Exim answers to.
  local host
  host="$(env_get PANEL_DOMAIN)"
  if [[ -z "$host" ]] || ! is_domain "$host"; then
    host="$(env_get PANEL_URL)"
    host="${host#*://}"; host="${host%%/*}"; host="${host%%:*}"
    host="${host,,}"
  fi
  if [[ -z "$host" ]] || ! is_domain "$host"; then
    host="$(hostname -f 2>/dev/null || hostname 2>/dev/null || true)"
  fi
  if [[ -z "$host" ]] || ! is_domain "$host"; then
    host="localhost.localdomain"
  fi
  printf '%s\n' "$host"
}

mail_write_file() {
  # mail_write_file <path> <owner:group> <mode>, content on stdin. Written
  # beside the target and renamed, so a reader never sees half a map.
  local path="$1" owner="$2" mode="$3" tmp
  tmp="$(mktemp "${path}.XXXXXX")"
  cat >"$tmp"
  chown "$owner" "$tmp"
  chmod "$mode" "$tmp"
  mv -f "$tmp" "$path"
}

mail_ensure_layout() {
  getent group vmail >/dev/null 2>&1 || groupadd --system vmail
  id vmail >/dev/null 2>&1 || useradd --system --gid vmail --home-dir "$MAIL_VMAIL_DIR" \
    --no-create-home --shell /usr/sbin/nologin vmail
  install -d -o vmail -g vmail -m 0770 "$MAIL_VMAIL_DIR"
  install -d -o root -g root -m 0755 "$MAIL_DIR" "${MAIL_DIR}/sieve"
  install -d -o root -g Debian-exim -m 0750 "${MAIL_DIR}/dkim" "${MAIL_DIR}/relay"
  local name
  # Exim's maps: addresses and domains, readable by the Exim user only.
  for name in domains mailboxes aliases catchall senders local_senders dkim_domains \
      relay_routes relay_hosts relay_transports relay_auth; do
    [[ -f "${MAIL_DIR}/${name}" ]] || : | mail_write_file "${MAIL_DIR}/${name}" root:Debian-exim 0640
  done
  # Dovecot's: password hashes, readable by the Dovecot auth process only.
  [[ -f "${MAIL_DIR}/passwd" ]] || : | mail_write_file "${MAIL_DIR}/passwd" root:dovecot 0640
  [[ -f "${MAIL_DIR}/denied" ]] || : | mail_write_file "${MAIL_DIR}/denied" root:dovecot 0640
  [[ -f "$MAIL_WEBMAIL_HOSTS" ]] || : | mail_write_file "$MAIL_WEBMAIL_HOSTS" root:root 0644
  [[ -f "$MAIL_SETTINGS_FILE" ]] || printf '{}\n' | mail_write_file "$MAIL_SETTINGS_FILE" root:root 0600
}

mail_tls_sync() {
  # Exim reads its certificate as Debian-exim, and /etc/opanel is root:opanel
  # 0750, so the mail daemons get their own copy of the panel certificate.
  # panel_cert_store_sync calls this, and every certificate issue and renewal
  # runs that, so a renewed certificate reaches IMAP and SMTP too.
  #   mail_tls_sync [--force] [--no-reload]
  local force=0 reload=1 arg cert key
  for arg in "$@"; do
    case "$arg" in
      --force) force=1 ;;
      --no-reload) reload=0 ;;
    esac
  done
  if [[ $force -eq 0 ]] && ! mail_installed; then
    return 0
  fi
  getent group Debian-exim >/dev/null 2>&1 || return 0
  cert="$(env_get PANEL_SSL_CERT)"; key="$(env_get PANEL_SSL_KEY)"
  if [[ -z "$cert" || -z "$key" || ! -f "$cert" || ! -f "$key" ]]; then
    panel_self_signed_ensure >/dev/null 2>&1 || true
    cert="${PANEL_CERT_STORE}/_default/fullchain.pem"
    key="${PANEL_CERT_STORE}/_default/privkey.pem"
  fi
  [[ -f "$cert" && -f "$key" ]] || return 0
  install -d -o root -g Debian-exim -m 0750 "${MAIL_DIR}/tls"
  if cmp -s "$cert" "${MAIL_DIR}/tls/fullchain.pem" && cmp -s "$key" "${MAIL_DIR}/tls/privkey.pem"; then
    return 0
  fi
  install -m 0640 -o root -g Debian-exim "$cert" "${MAIL_DIR}/tls/fullchain.pem"
  install -m 0640 -o root -g Debian-exim "$key" "${MAIL_DIR}/tls/privkey.pem"
  if [[ $reload -eq 1 ]]; then
    # Exim opens the files per connection; Dovecot keeps them until reloaded.
    systemctl reload dovecot >/dev/null 2>&1 || true
  fi
  return 0
}

mail_write_exim_config() {
  local host target="/etc/exim4/exim4.conf" check
  host="$(mail_hostname)"
  install -d -o root -g root -m 0755 /etc/exim4
  python3 - "$MAIL_SETTINGS_FILE" "$host" "${target}.new" "$MAIL_DIR" <<'PY'
import json
import re
import sys
from pathlib import Path

settings_path, host, out_path, mail_dir = sys.argv[1:5]
try:
    raw = json.loads(Path(settings_path).read_text(encoding="utf-8"))
except Exception:
    raw = {}
if not isinstance(raw, dict):
    raw = {}


def whole(value, low, high, fallback):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if low <= number <= high else fallback


auth_rate = whole(raw.get("auth_rate_per_hour"), 0, 100000, 300)
local_rate = whole(raw.get("local_rate_per_hour"), 0, 100000, 300)
max_mb = whole(raw.get("max_message_mb"), 1, 200, 50)

M = mail_dir
# The domain a message is sent for: the one it is DKIM-signed for, else the
# envelope sender's. Outgoing relays are chosen by it.
SEND_DOMAIN = "${if def:acl_m_dkim{$acl_m_dkim}{${lc:$sender_address_domain}}}"
conf = f"""# Managed by OPanel (Email addon). Rewritten whenever the mail settings
# change -- edit them in the panel, not here.
primary_hostname = {host}
qualify_domain = {host}
smtp_banner = $smtp_active_hostname ESMTP $tod_full

domainlist local_domains = @ : localhost : localhost.localdomain
domainlist virtual_domains = lsearch;{M}/domains
domainlist relay_to_domains =
hostlist   relay_from_hosts = <; 127.0.0.1 ; ::1

acl_smtp_mail = acl_check_mail
acl_smtp_rcpt = acl_check_rcpt
acl_smtp_data = acl_check_data
acl_not_smtp = acl_check_not_smtp

spamd_address = 127.0.0.1 11333 variant=rspamd

daemon_smtp_ports = 25 : 465 : 587
tls_on_connect_ports = 465
tls_advertise_hosts = *
tls_certificate = {M}/tls/fullchain.pem
tls_privatekey = {M}/tls/privkey.pem

message_size_limit = {max_mb}M
smtp_accept_max = 100
smtp_accept_max_per_host = 20
host_lookup = *
rfc1413_hosts =
prdr_enable = true
# PHP's mail() may set its own envelope sender (-f); a sender is only ever
# signed for when it is one of the caller's own domains -- see acl_check_not_smtp.
untrusted_set_sender = *
local_from_check = false
keep_environment =
add_environment = <; PATH=/bin:/usr/bin
ignore_bounce_errors_after = 2d
timeout_frozen_after = 7d
log_selector = +smtp_protocol_error +smtp_syntax_error +incoming_port +outgoing_port +tls_peerdn

begin acl

acl_check_mail:
  # A signed-in mailbox may send only as an address at one of its owner's
  # domains, so one customer cannot send as another.
  deny    authenticated = *
          !condition    = ${{if inlisti{{${{lc:$sender_address_domain}}}}{{${{lookup{{${{lc:$authenticated_id}}}}lsearch{{{M}/senders}}{{$value}}{{}}}}}}}}
          message       = Your login cannot send as <$sender_address>
  accept

acl_check_rcpt:
  accept  hosts         = :
          control       = dkim_disable_verify

  deny    domains       = +local_domains : +virtual_domains
          local_parts   = ^[.] : ^.*[@%!/|]
          message       = Restricted characters in address

  deny    domains       = ! +local_domains : ! +virtual_domains
          local_parts   = ^[./|] : ^.*[@%!] : ^.*/\\\\.\\\\./
          message       = Restricted characters in address
"""
if auth_rate:
    conf += f"""
  deny    authenticated = *
          ratelimit     = {auth_rate} / 1h / per_rcpt / $authenticated_id
          message       = Sending limit reached ({auth_rate} recipients an hour). Try again later.
"""
conf += f"""
  accept  authenticated = *
          control       = submission/sender_retain
          control       = dkim_disable_verify

  # The submission ports are for signed-in clients only.
  deny    condition     = ${{if or{{{{eq{{$received_port}}{{587}}}}{{eq{{$received_port}}{{465}}}}}}}}
          message       = Authentication required

  accept  hosts         = +relay_from_hosts
          control       = submission/sender_retain
          control       = dkim_disable_verify

  require message       = Relay not permitted
          domains       = +local_domains : +virtual_domains

  require verify        = recipient

  accept

acl_check_data:
  # The From: header must be one of the login's domains as well; a message
  # that passes is DKIM-signed for that domain on its way out.
  deny    authenticated = *
          !condition    = ${{if inlisti{{${{lc:${{domain:$h_from:}}}}}}{{${{lookup{{${{lc:$authenticated_id}}}}lsearch{{{M}/senders}}{{$value}}{{}}}}}}}}
          message       = The From: address is not one of your mail domains

  accept  authenticated = *
          set acl_m_dkim = ${{lc:${{domain:$h_from:}}}}

  accept  hosts         = : +relay_from_hosts

  # Rspamd: SPF, DKIM, DMARC, DNS blocklists, greylisting and the Bayes filter.
  accept  condition     = ${{if >{{$message_size}}{{20M}}}}

  warn    spam          = nobody:true/defer_ok

  defer   condition     = ${{if eq{{$spam_action}}{{greylist}}}}
          message       = Greylisted, please try again later

  defer   condition     = ${{if eq{{$spam_action}}{{soft reject}}}}
          message       = Please try again later

  deny    condition     = ${{if eq{{$spam_action}}{{reject}}}}
          message       = Message rejected as spam

  warn    condition     = ${{if or{{{{eq{{$spam_action}}{{add header}}}}{{eq{{$spam_action}}{{rewrite subject}}}}}}}}
          add_header    = X-Spam-Status: Yes, score=$spam_score

  warn    condition     = ${{if def:spam_score}}
          add_header    = X-Spam-Score: $spam_score

  accept

acl_check_not_smtp:
  # Mail from a website's PHP (sendmail) is signed only for a domain the calling
  # Linux user's panel account owns.
"""
if local_rate:
    conf += f"""  deny    !condition    = ${{if eq{{$sender_ident}}{{root}}}}
          ratelimit     = {local_rate} / 1h / per_mail / $sender_ident
          message       = Sending limit reached ({local_rate} messages an hour)

"""
conf += f"""  warn    condition     = ${{if inlisti{{${{lc:${{domain:$h_from:}}}}}}{{${{lookup{{$sender_ident}}lsearch{{{M}/local_senders}}{{$value}}{{}}}}}}}}
          set acl_m_dkim = ${{lc:${{domain:$h_from:}}}}
  accept

begin routers
"""
conf += f"""
# Outgoing relays (smarthosts). relay_routes maps a sending domain to a relay
# id, "*" to the server's default relay and "direct" to delivery without one;
# relay_hosts and relay_transports say where each relay is and how to talk to
# it. With no route the message goes out directly (dnslookup).
relay:
  driver = manualroute
  domains = ! +local_domains : ! +virtual_domains
  condition = ${{if !eq{{${{lookup{{{SEND_DOMAIN}}}lsearch*{{{M}/relay_routes}}{{$value}}{{direct}}}}}}{{direct}}}}
  address_data = ${{lookup{{{SEND_DOMAIN}}}lsearch*{{{M}/relay_routes}}}}
  route_data = ${{lookup{{$address_data}}lsearch{{{M}/relay_hosts}}}}
  transport = ${{lookup{{$address_data}}lsearch{{{M}/relay_transports}}{{$value}}{{remote_smtp_relay}}}}
  host_find_failed = defer
  same_domain_copy_routing = yes
  no_more

dnslookup:
  driver = dnslookup
  domains = ! +local_domains : ! +virtual_domains
  transport = remote_smtp
  ignore_target_hosts = <; 0.0.0.0 ; 127.0.0.0/8 ; ::1
  no_more
"""
conf += f"""
# Forwarders. An address that is also a mailbox lists itself among the
# targets; Exim passes an address redirected to itself on to the next router,
# which delivers the copy.
virtual_forward:
  driver = redirect
  domains = +virtual_domains
  data = ${{lookup{{${{lc:$local_part@$domain}}}}lsearch{{{M}/aliases}}}}
  forbid_file
  forbid_pipe
  allow_fail
  allow_defer

virtual_mailbox:
  driver = accept
  domains = +virtual_domains
  condition = ${{lookup{{${{lc:$local_part@$domain}}}}lsearch{{{M}/mailboxes}}{{true}}{{false}}}}
  transport = dovecot_lmtp

virtual_catchall:
  driver = redirect
  domains = +virtual_domains
  data = ${{lookup{{${{lc:$domain}}}}lsearch{{{M}/catchall}}}}
  forbid_file
  forbid_pipe

system_aliases:
  driver = redirect
  domains = +local_domains
  data = ${{lookup{{$local_part}}lsearch{{/etc/aliases}}}}
  forbid_file
  forbid_pipe
  allow_fail
  allow_defer

local_user:
  driver = accept
  domains = +local_domains
  check_local_user
  transport = local_delivery
  cannot_route_message = Unknown user

begin transports

remote_smtp:
  driver = smtp
  helo_data = {host}
  dkim_domain = ${{lookup{{$acl_m_dkim}}lsearch{{{M}/dkim_domains}}{{$value}}{{}}}}
  dkim_selector = opanel
  dkim_private_key = ${{lookup{{$acl_m_dkim}}lsearch{{{M}/dkim_domains}}{{{M}/dkim/$value.key}}{{0}}}}
  dkim_canon = relaxed
  dkim_strict = false
"""
# One transport per way of reaching a relay. A relay with a login is listed
# in relay_auth by host, which is what makes Exim authenticate to it.
relay_modes = (
    ("remote_smtp_relay", "  hosts_require_tls = *\n  tls_verify_certificates = system\n  tls_verify_hosts = *\n"),
    ("remote_smtps_relay", "  protocol = smtps\n  hosts_require_tls = *\n  tls_verify_certificates = system\n  tls_verify_hosts = *\n"),
    ("remote_smtp_relay_plain", ""),
)
for name, tls in relay_modes:
    conf += f"""
{name}:
  driver = smtp
  helo_data = {host}
{tls}  hosts_require_auth = ${{lookup{{$host}}lsearch{{{M}/relay_auth}}{{*}}{{}}}}
  dkim_domain = ${{lookup{{$acl_m_dkim}}lsearch{{{M}/dkim_domains}}{{$value}}{{}}}}
  dkim_selector = opanel
  dkim_private_key = ${{lookup{{$acl_m_dkim}}lsearch{{{M}/dkim_domains}}{{{M}/dkim/$value.key}}{{0}}}}
  dkim_canon = relaxed
  dkim_strict = false
"""
conf += f"""
dovecot_lmtp:
  driver = lmtp
  socket = /run/dovecot/exim-lmtp
  batch_max = 200

local_delivery:
  driver = appendfile
  file = /var/mail/$local_part_data
  delivery_date_add
  envelope_to_add
  return_path_add
  group = mail
  mode = 0660

begin retry

*                      *           F,2h,15m; G,16h,1h,1.5; F,4d,6h

begin rewrite

begin authenticators

# Mailbox logins are checked by Dovecot, so Exim never sees a password file.
# AUTH is offered only inside TLS, or to the webmail on loopback.
dovecot_plain:
  driver = dovecot
  public_name = PLAIN
  server_socket = /run/dovecot/exim-auth
  server_set_id = $auth1
  server_advertise_condition = ${{if or{{{{def:tls_in_cipher}}{{match_ip{{$sender_host_address}}{{<; 127.0.0.1 ; ::1}}}}}}}}

dovecot_login:
  driver = dovecot
  public_name = LOGIN
  server_socket = /run/dovecot/exim-auth
  server_set_id = $auth1
  server_advertise_condition = ${{if or{{{{def:tls_in_cipher}}{{match_ip{{$sender_host_address}}{{<; 127.0.0.1 ; ::1}}}}}}}}
"""
# Relay logins, read per relay from /etc/opanel-mail/relay/<id>.user|.pass.
# PLAIN first; LOGIN for relays that offer only that (Office 365).
user_part = f"${{sg{{${{readfile{{{M}/relay/${{lookup{{$host}}lsearch{{{M}/relay_auth}}{{$value}}{{none}}}}.user}}{{}}}}}}{{\\\\^}}{{^^}}}}"
pass_part = f"${{sg{{${{readfile{{{M}/relay/${{lookup{{$host}}lsearch{{{M}/relay_auth}}{{$value}}{{none}}}}.pass}}{{}}}}}}{{\\\\^}}{{^^}}}}"
conf += f"""
relay_plain:
  driver = plaintext
  public_name = PLAIN
  client_send = ^{user_part}^{pass_part}

relay_login:
  driver = plaintext
  public_name = LOGIN
  client_send = : {user_part} : {pass_part}
"""
Path(out_path).write_text(conf, encoding="utf-8")
PY
  chmod 0644 "${target}.new"
  if ! check="$(exim4 -C "${target}.new" -bV 2>&1)"; then
    rm -f "${target}.new"
    deny "the generated Exim configuration is invalid: $(printf '%s' "$check" | tail -n 3 | tr '\n' ' ')"
  fi
  mv -f "${target}.new" "$target"
}

mail_write_relay_credentials() {
  # The relays from settings.json: where each is (relay_hosts), how Exim talks
  # to it (relay_transports), which hosts need a login (relay_auth) and the
  # logins themselves, one pair of files per relay so that no character in a
  # password can break the configuration. Which domain uses which relay is
  # mail-sync's (relay_routes).
  python3 - "$MAIL_SETTINGS_FILE" "$MAIL_DIR" <<'PY'
import grp
import json
import os
import re
import sys
from pathlib import Path

settings_path, mail_dir = sys.argv[1:3]
mail_dir = Path(mail_dir)
try:
    raw = json.loads(Path(settings_path).read_text(encoding="utf-8"))
except Exception:
    raw = {}
relays = raw.get("relays") if isinstance(raw, dict) and isinstance(raw.get("relays"), list) else []

ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$|^(?:\d{1,3}\.){3}\d{1,3}$")
TRANSPORT = {"starttls": "remote_smtp_relay", "ssl": "remote_smtps_relay", "none": "remote_smtp_relay_plain"}
gid = grp.getgrnam("Debian-exim").gr_gid


def clean(value):
    value = str(value or "")
    return "" if any(ch in value for ch in "\r\n\0") or len(value) > 255 else value


def write(path, text, mode=0o640):
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chown(tmp, 0, gid)
    os.chmod(tmp, mode)
    tmp.replace(path)


hosts, transports, auth = [], [], []
logins = {}
seen_auth_hosts = set()
for relay in relays:
    if not isinstance(relay, dict):
        continue
    rid = str(relay.get("id") or "")
    host = str(relay.get("host") or "").strip().lower()
    try:
        port = int(relay.get("port") or 587)
    except (TypeError, ValueError):
        port = 0
    tls = str(relay.get("tls") or "starttls")
    if not ID.fullmatch(rid) or not HOST.fullmatch(host) or not 1 <= port <= 65535 or tls not in TRANSPORT:
        sys.exit(f"opanel-helper: invalid relay {rid or host!r}")
    hosts.append(f"{rid}: {host}::{port} byname")
    transports.append(f"{rid}: {TRANSPORT[tls]}")
    user, password = clean(relay.get("username")), clean(relay.get("password"))
    if user:
        if host in seen_auth_hosts:
            sys.exit(f"opanel-helper: two relays with a login on {host}")
        seen_auth_hosts.add(host)
        auth.append(f"{host}: {rid}")
        logins[rid] = (user, password)

relay_dir = mail_dir / "relay"
relay_dir.mkdir(mode=0o750, exist_ok=True)
os.chown(relay_dir, 0, gid)
os.chmod(relay_dir, 0o750)
for rid, (user, password) in logins.items():
    write(relay_dir / f"{rid}.user", user)
    write(relay_dir / f"{rid}.pass", password)
for stale in relay_dir.iterdir():
    if stale.suffix in (".user", ".pass") and stale.stem not in logins:
        stale.unlink()
write(mail_dir / "relay_hosts", "".join(line + "\n" for line in hosts))
write(mail_dir / "relay_transports", "".join(line + "\n" for line in transports))
write(mail_dir / "relay_auth", "".join(line + "\n" for line in auth))
# The single-relay files of the first Email release.
for old in ("relay.user", "relay.pass"):
    (mail_dir / old).unlink(missing_ok=True)
print(f"{len(hosts)} relays written")
PY
}

mail_write_dovecot_config() {
  local host vmail_uid listen="*" dh="" target="/etc/dovecot/dovecot.conf" check
  host="$(mail_hostname)"
  vmail_uid="$(id -u vmail)"
  if [[ -f /proc/net/if_inet6 ]]; then listen="*, ::"; fi
  if [[ -f /usr/share/dovecot/dh.pem ]]; then dh="ssl_dh = </usr/share/dovecot/dh.pem"; fi
  install -d -o root -g root -m 0755 /etc/dovecot
  cat >"${target}.new" <<DOVECOT
# Managed by OPanel (Email addon). Rewritten when the addon is installed or
# updated; conf.d is not read.
protocols = imap pop3 lmtp
listen = ${listen}
login_greeting = Mail server ready.

mail_home = ${MAIL_VMAIL_DIR}/%Ld/%Ln
mail_location = maildir:~/Maildir
mail_uid = vmail
mail_gid = vmail
first_valid_uid = ${vmail_uid}
last_valid_uid = ${vmail_uid}
mail_privileged_group = mail
mail_plugins = \$mail_plugins quota

ssl = yes
ssl_cert = <${MAIL_DIR}/tls/fullchain.pem
ssl_key = <${MAIL_DIR}/tls/privkey.pem
ssl_min_protocol = TLSv1.2
ssl_prefer_server_ciphers = yes
${dh}

# Passwords only inside TLS -- loopback, where the webmail connects, counts as
# secure.
disable_plaintext_auth = yes
auth_mechanisms = plain login
auth_username_format = %Lu
auth_master_user_separator = *

# The webmail's single sign-on: "mailbox*${MAIL_SSO_MASTER}" with the master
# password, accepted from loopback only (allow_nets in the file). pass = yes
# still looks the mailbox up below, so a suspended one stays shut.
passdb {
  driver = passwd-file
  args = ${MAIL_DIR}/master-users
  master = yes
  pass = yes
}
# Suspended mailboxes: listed here, refused before any password is checked.
passdb {
  driver = passwd-file
  args = username_format=%Lu ${MAIL_DIR}/denied
  deny = yes
}
passdb {
  driver = passwd-file
  args = username_format=%Lu ${MAIL_DIR}/passwd
}
userdb {
  driver = passwd-file
  args = username_format=%Lu ${MAIL_DIR}/passwd
}

namespace inbox {
  inbox = yes
  mailbox Drafts {
    auto = subscribe
    special_use = \Drafts
  }
  mailbox Sent {
    auto = subscribe
    special_use = \Sent
  }
  mailbox Junk {
    auto = subscribe
    special_use = \Junk
  }
  mailbox Trash {
    auto = subscribe
    special_use = \Trash
  }
}

protocol imap {
  mail_plugins = \$mail_plugins imap_quota
  mail_max_userip_connections = 50
}
protocol lmtp {
  mail_plugins = \$mail_plugins sieve
  postmaster_address = postmaster@${host}
}
lmtp_rcpt_check_quota = yes

plugin {
  quota = count:User quota
  quota_vsizes = yes
  quota_grace = 10%%
  quota_exceeded_message = The mailbox is full.
  sieve_before = ${MAIL_DIR}/sieve/spam.sieve
  sieve = file:~/sieve;active=~/.dovecot.sieve
}

service lmtp {
  unix_listener exim-lmtp {
    mode = 0660
    user = Debian-exim
    group = Debian-exim
  }
}
service auth {
  unix_listener exim-auth {
    mode = 0660
    user = Debian-exim
    group = Debian-exim
  }
}
service imap-login {
  inet_listener imap {
    port = 143
  }
  inet_listener imaps {
    port = 993
    ssl = yes
  }
}
service pop3-login {
  inet_listener pop3 {
    port = 110
  }
  inet_listener pop3s {
    port = 995
    ssl = yes
  }
}
DOVECOT
  chmod 0644 "${target}.new"
  if ! check="$(doveconf -n -c "${target}.new" 2>&1 >/dev/null)"; then
    rm -f "${target}.new"
    deny "the generated Dovecot configuration is invalid: $(printf '%s' "$check" | tail -n 3 | tr '\n' ' ')"
  fi
  mv -f "${target}.new" "$target"
}

mail_write_sieve() {
  mail_write_file "${MAIL_DIR}/sieve/spam.sieve" root:root 0644 <<'SIEVE'
# Managed by OPanel: mail Rspamd marked as spam goes to Junk.
require ["fileinto"];
if header :contains "X-Spam-Status" "Yes" {
  fileinto "Junk";
  stop;
}
SIEVE
  sievec "${MAIL_DIR}/sieve/spam.sieve" >/dev/null 2>&1 || deny "could not compile the spam filter rule"
  chmod 0644 "${MAIL_DIR}/sieve/spam.svbin" 2>/dev/null || true
}

mail_write_unbound_config() {
  install -d -o root -g root -m 0755 /etc/unbound/unbound.conf.d
  mail_write_file "$MAIL_UNBOUND_CONF" root:root 0644 <<UNBOUND
# Managed by OPanel (Email addon): a resolver for Rspamd only.
server:
  interface: 127.0.0.1
  port: ${MAIL_UNBOUND_PORT}
  access-control: 127.0.0.0/8 allow
  access-control: 0.0.0.0/0 refuse
  hide-identity: yes
  hide-version: yes
  prefetch: yes
UNBOUND
  # The package's hook would register 127.0.0.1 as the system resolver where
  # resolvconf is installed; this resolver is Rspamd's alone.
  systemctl disable --now unbound-resolvconf.service >/dev/null 2>&1 || true
  systemctl mask unbound-resolvconf.service >/dev/null 2>&1 || true
  if command -v unbound-checkconf >/dev/null 2>&1; then
    unbound-checkconf >/dev/null 2>&1 || deny "the generated Unbound configuration is invalid"
  fi
}

mail_write_rspamd_config() {
  local greylist="4" reject="15" header="6"
  read -r greylist header reject < <(python3 - "$MAIL_SETTINGS_FILE" <<'PY'
import json
import sys
from pathlib import Path

try:
    raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    raw = {}
raw = raw if isinstance(raw, dict) else {}


def score(value, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if 1 <= number <= 100 else fallback


header = score(raw.get("spam_header_score"), 6.0)
reject = score(raw.get("spam_reject_score"), 15.0)
if reject <= header:
    reject = header + 1
greylist = "null" if raw.get("greylisting") is False else "4"
print(greylist, f"{header:g}", f"{reject:g}")
PY
)
  install -d -o root -g root -m 0755 /etc/rspamd/local.d
  mail_write_file /etc/rspamd/local.d/actions.conf root:root 0644 <<RSPAMD
# Managed by OPanel (Email addon).
reject = ${reject};
add_header = ${header};
greylist = ${greylist};
RSPAMD
  # Greylisting, rate limits and the Bayes filter keep their state in Redis. A
  # database of its own so it never mixes with the panel's or a site's keys.
  mail_write_file /etc/rspamd/local.d/redis.conf root:root 0644 <<'RSPAMD'
# Managed by OPanel (Email addon).
servers = "127.0.0.1:6379";
db = "9";
RSPAMD
  mail_write_file /etc/rspamd/local.d/classifier-bayes.conf root:root 0644 <<'RSPAMD'
# Managed by OPanel (Email addon).
autolearn = true;
RSPAMD
  # Exim signs outgoing mail itself; Rspamd only scans what arrives.
  mail_write_file /etc/rspamd/local.d/dkim_signing.conf root:root 0644 <<'RSPAMD'
# Managed by OPanel (Email addon).
enabled = false;
RSPAMD
  mail_write_file /etc/rspamd/local.d/arc.conf root:root 0644 <<'RSPAMD'
# Managed by OPanel (Email addon).
enabled = false;
RSPAMD
  mail_write_file /etc/rspamd/local.d/logging.inc root:root 0644 <<'RSPAMD'
# Managed by OPanel (Email addon). "notice" logs one line per scanned
# message, which the Rspamd page of the panel reads.
level = "notice";
RSPAMD
  mail_write_file /etc/rspamd/local.d/options.inc root:root 0644 <<RSPAMD
# Managed by OPanel (Email addon): DNS blocklists need a resolver of our own.
dns {
  nameserver = ["127.0.0.1:${MAIL_UNBOUND_PORT}"];
}
RSPAMD
}

mail_env_value() {
  [[ -f "$MAIL_WEBMAIL_ENV" ]] || return 0
  awk -F= -v key="$1" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$MAIL_WEBMAIL_ENV"
}

mail_webmail_write_env() {
  # Secrets survive a reinstall, so webmail sessions and the panel's SSO keep
  # working across one.
  local auth_secret sso_secret master_password hash fresh=0
  auth_secret="$(mail_env_value AUTH_SECRET)"
  sso_secret="$(mail_env_value SSO_SECRET)"
  master_password="$(mail_env_value SSO_MASTER_PASSWORD)"
  [[ ${#auth_secret} -ge 32 ]] || auth_secret="$(openssl rand -hex 32)"
  [[ ${#sso_secret} -ge 32 ]] || sso_secret="$(openssl rand -hex 32)"
  if [[ ${#master_password} -lt 24 ]]; then
    master_password="$(openssl rand -hex 24)"
    fresh=1
  fi
  mail_write_file "$MAIL_WEBMAIL_ENV" root:bnix-webmail 0640 <<ENV
# Managed by OPanel (Email addon).
HOST=127.0.0.1
PORT=${MAIL_WEBMAIL_PORT}
DATA_DIR=${MAIL_WEBMAIL_ROOT}/data
AUTH_SECRET=${auth_secret}
IMAP_HOST=127.0.0.1
IMAP_PORT=143
IMAP_SECURE=false
SMTP_HOST=127.0.0.1
SMTP_PORT=587
SMTP_SECURE=false
ENABLE_CADDY_AUTOMATION=false
SSO_SECRET=${sso_secret}
SSO_MASTER_USER=${MAIL_SSO_MASTER}
SSO_MASTER_PASSWORD=${master_password}
ENV
  install -d -o opanel -g opanel -m 0750 "${opanel_DATA_DIR}/addons"
  printf '%s\n' "$sso_secret" | mail_write_file "$MAIL_SSO_SECRET_FILE" opanel:opanel 0600
  if [[ $fresh -eq 0 && -s "${MAIL_DIR}/master-users" ]]; then
    return 0
  fi
  hash="$(printf '%s' "$master_password" | openssl passwd -6 -stdin)" || deny "could not hash the webmail sign-on password"
  printf '%s:{SHA512-CRYPT}%s::::::allow_nets=127.0.0.1/32,::1/128\n' "$MAIL_SSO_MASTER" "$hash" \
    | mail_write_file "${MAIL_DIR}/master-users" root:dovecot 0640
}

mail_webmail_install() {
  local src="${MAIL_WEBMAIL_ROOT}/src" venv="${MAIL_WEBMAIL_ROOT}/venv"
  getent group bnix-webmail >/dev/null 2>&1 || groupadd --system bnix-webmail
  id bnix-webmail >/dev/null 2>&1 || useradd --system --gid bnix-webmail --home-dir "$MAIL_WEBMAIL_ROOT" \
    --no-create-home --shell /usr/sbin/nologin bnix-webmail
  install -d -o root -g root -m 0755 "$MAIL_WEBMAIL_ROOT"
  if [[ ! -d "${src}/.git" ]]; then
    rm -rf "${src}.tmp"
    git clone --quiet "$MAIL_WEBMAIL_REPO" "${src}.tmp" >/dev/null 2>&1 \
      || { rm -rf "${src}.tmp"; deny "could not download the webmail from ${MAIL_WEBMAIL_REPO}"; }
    rm -rf "$src"
    mv "${src}.tmp" "$src"
  fi
  if [[ "$(git -C "$src" rev-parse HEAD 2>/dev/null || true)" != "$MAIL_WEBMAIL_COMMIT" ]]; then
    git -C "$src" fetch --quiet origin >/dev/null 2>&1 || true
    git -C "$src" -c advice.detachedHead=false checkout --quiet --force "$MAIL_WEBMAIL_COMMIT" >/dev/null 2>&1 \
      || deny "webmail version ${MAIL_WEBMAIL_COMMIT:0:7} is not available from ${MAIL_WEBMAIL_REPO}"
  fi
  [[ "$(git -C "$src" rev-parse HEAD)" == "$MAIL_WEBMAIL_COMMIT" ]] || deny "the webmail checkout does not match the pinned version"
  chown -R root:root "$src"
  if [[ ! -x "${venv}/bin/python" ]]; then
    python3 -m venv "$venv" || deny "could not create the webmail's Python environment"
  fi
  local wanted
  wanted="$(sha256sum "${src}/backend/requirements.txt" | cut -d' ' -f1)"
  if [[ "$(cat "${venv}/.opanel-requirements" 2>/dev/null || true)" != "$wanted" ]]; then
    "${venv}/bin/pip" install --quiet --disable-pip-version-check -r "${src}/backend/requirements.txt" >/dev/null \
      || deny "could not install the webmail's Python packages"
    printf '%s\n' "$wanted" >"${venv}/.opanel-requirements"
  fi
  install -d -o bnix-webmail -g bnix-webmail -m 0750 "${MAIL_WEBMAIL_ROOT}/data"
  mail_webmail_write_env
  cat >"$MAIL_WEBMAIL_UNIT" <<UNIT
# Managed by OPanel (Email addon).
[Unit]
Description=BNIX Webmail (OPanel Email addon)
After=network.target dovecot.service exim4.service

[Service]
Type=simple
User=bnix-webmail
Group=bnix-webmail
WorkingDirectory=${src}/backend
EnvironmentFile=${MAIL_WEBMAIL_ENV}
ExecStart=${venv}/bin/python ${src}/backend/main.py
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=${MAIL_WEBMAIL_ROOT}/data

[Install]
WantedBy=multi-user.target
UNIT
  chmod 0644 "$MAIL_WEBMAIL_UNIT"
  systemctl daemon-reload
}

mail_webmail_vhost_body() {
  # mail_webmail_vhost_body <vhDomain> [cert key]. The webmail's own admin
  # area manages Caddy and its own domain list, neither of which exists here,
  # so it is not reachable.
  local vh_domain="$1" cert="${2:-}" key="${3:-}"
  cat <<VHOST
# opanel-mail webmail vhost -- managed by OPanel (Email addon).
docRoot                   ${MAIL_WEBMAIL_DOCROOT}/
vhDomain                  ${vh_domain}
enableIpGeo               0
enableGzip                1

extprocessor bnixwebmail {
  type                    proxy
  address                 127.0.0.1:${MAIL_WEBMAIL_PORT}
  maxConns                200
  pcKeepAliveTimeout      60
  initTimeout             60
  retryTimeout            0
  respBuffer              0
}

context /.well-known/acme-challenge/ {
  type                    static
  location                /var/www/opanel-acme/.well-known/acme-challenge/
  allowBrowse             1
  addDefaultCharset       off
}

context / {
  type                    proxy
  handler                 bnixwebmail
  addDefaultCharset       off
}

rewrite  {
  enable                  1
  autoLoadHtaccess        0
  rules                   <<<END_rules
RewriteRule ^/?(admin|api/admin)(/.*)?$ - [F,L]
VHOST
  if [[ -n "$cert" ]]; then
    cat <<'VHOST'
RewriteCond %{HTTPS} !=on
RewriteCond %{REQUEST_URI} !^/\.well-known/acme-challenge/
RewriteRule ^(.*)$ https://%{HTTP_HOST}$1 [R=301,L]
VHOST
  fi
  cat <<'VHOST'
  END_rules
}
VHOST
  if [[ -n "$cert" ]]; then
    cat <<VHOST

vhssl  {
  keyFile                 ${key}
  certFile                ${cert}
  certChain               1
}
VHOST
  fi
}

mail_write_webmail_vhost() {
  install -d -o root -g root -m 0755 "$MAIL_WEBMAIL_DOCROOT"
  mail_webmail_vhost_body "$(mail_hostname)" | mail_write_file "$MAIL_WEBMAIL_VHOST" root:root 0644
}

mail_webmail_host_add() {
  # webmail.<domain>: its own vhost, answered over HTTP until Let's Encrypt
  # has issued its certificate, then HTTPS with HTTP redirected.
  local domain="$1" email="${2:-}" host dir
  host="webmail.${domain}"
  require_domain "$host"
  dir="${OLS_VHOSTS_DIR}/${host}"
  if [[ -f "${dir}/vhost.conf" ]] && ! grep -q '^# opanel-mail webmail vhost' "${dir}/vhost.conf"; then
    deny "${host} is already a website on this server"
  fi
  install -d -o root -g root -m 0755 "$MAIL_WEBMAIL_DOCROOT" "$dir"
  install -d -o root -g opanel -m 0755 /var/www/opanel-acme/.well-known/acme-challenge
  if [[ ! -f "/etc/letsencrypt/live/${host}/fullchain.pem" ]]; then
    mail_webmail_vhost_body "$host" | mail_write_file "${dir}/vhost.conf" root:root 0644
    ols_sync_main_config
    restart_openlitespeed
    local -a args=(certonly --webroot -w /var/www/opanel-acme --cert-name "$host" -d "$host"
      --non-interactive --agree-tos)
    if [[ -n "$email" ]]; then
      require_email "$email"
      args+=(--email "$email")
    else
      args+=(--register-unsafely-without-email)
    fi
    if ! certbot "${args[@]}" >/dev/null 2>&1; then
      # No certificate, no webmail on this name: a sign-in page over plain
      # HTTP is worse than none.
      rm -rf "${dir:?}"
      ols_sync_main_config
      restart_openlitespeed
      deny "Let's Encrypt could not issue a certificate for ${host}. Point its A record at this server, wait for DNS, then try again."
    fi
  fi
  mail_webmail_vhost_body "$host" "/etc/letsencrypt/live/${host}/fullchain.pem" "/etc/letsencrypt/live/${host}/privkey.pem" \
    | mail_write_file "${dir}/vhost.conf" root:root 0644
  grep -qxF "$host" "$MAIL_WEBMAIL_HOSTS" 2>/dev/null || printf '%s\n' "$host" >>"$MAIL_WEBMAIL_HOSTS"
  ols_sync_main_config
  restart_openlitespeed
  panel_cert_store_sync >/dev/null 2>&1 || true
  echo "webmail is served at https://${host}/"
}

mail_webmail_host_remove() {
  local host="$1" dir
  require_domain "$host"
  [[ "$host" == webmail.* ]] || deny "not a webmail host: $host"
  dir="${OLS_VHOSTS_DIR}/${host}"
  if [[ -f "${dir}/vhost.conf" ]] && grep -q '^# opanel-mail webmail vhost' "${dir}/vhost.conf"; then
    rm -rf "${dir:?}"
  fi
  certbot delete --cert-name "$host" --non-interactive >/dev/null 2>&1 || true
  if [[ -f "$MAIL_WEBMAIL_HOSTS" ]]; then
    grep -vxF "$host" "$MAIL_WEBMAIL_HOSTS" | mail_write_file "$MAIL_WEBMAIL_HOSTS" root:root 0644 || true
  fi
}

mail_open_ports() {
  local p
  for p in "${MAIL_EXTRA_PORTS[@]}"; do
    iptables_panel_allow_port "$p" 2>/dev/null || true
  done
  iptables_restore_addon_precedence 2>/dev/null || true
  firewall_persist_rules
}

mail_close_ports() {
  local p
  for p in "${MAIL_EXTRA_PORTS[@]}"; do
    iptables_panel_delete_port_rules "$p" "opanel:PanelZone" 2>/dev/null || true
  done
  firewall_persist_rules
}

mail_sync_from_stdin() {
  # The full mail state as JSON on stdin; see app/services/mail.py for the
  # shape. Every field is validated again here, and nothing is written unless
  # all of it passes.
  #
  # The payload is saved first: the Python below is itself a heredoc, which
  # takes stdin, so it could never read the panel's JSON there.
  local payload rc=0
  payload="$(mktemp "${MAIL_DIR}/.sync.XXXXXX")"
  chmod 0600 "$payload"
  cat >"$payload"
  python3 - "$MAIL_DIR" "$payload" <<'PY' || rc=$?
import grp
import json
import os
import re
import sys
from pathlib import Path

mail_dir = Path(sys.argv[1])
payload_path = Path(sys.argv[2])
DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
LOCAL = re.compile(r"^[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9_+-])?$")
REMOTE = re.compile(r"^[A-Za-z0-9._%+=-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
HASH = re.compile(r"^\{(?:BLF-CRYPT|SHA512-CRYPT)\}\$[A-Za-z0-9./$]{20,200}$")
LINUX_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

try:
    data = json.loads(payload_path.read_text(encoding="utf-8"))
except ValueError:
    sys.exit("opanel-helper: mail-sync input is not JSON")
if not isinstance(data, dict):
    sys.exit("opanel-helper: mail-sync input must be an object")


def fail(message):
    sys.exit(f"opanel-helper: mail-sync: {message}")


def address(value, what):
    value = str(value or "").strip().lower()
    local, _, domain = value.partition("@")
    if not LOCAL.fullmatch(local) or ".." in local or not DOMAIN.fullmatch(domain) or len(value) > 254:
        fail(f"invalid {what}: {value!r}")
    return value


def remote(value):
    value = str(value or "").strip()
    if not REMOTE.fullmatch(value) or len(value) > 254 or ".." in value:
        fail(f"invalid destination: {value!r}")
    return value.lower()


domains = []
for item in data.get("domains") or []:
    name = str((item or {}).get("domain") or "").strip().lower()
    if not DOMAIN.fullmatch(name):
        fail(f"invalid domain: {name!r}")
    if name in domains:
        fail(f"duplicate domain: {name}")
    domains.append(name)
domain_set = set(domains)

catchall = {}
for item in data.get("domains") or []:
    target = str(item.get("catch_all") or "").strip()
    if target:
        catchall[str(item["domain"]).lower()] = remote(target)

mailboxes = {}
passwd = []
denied = []
for item in data.get("mailboxes") or []:
    addr = address(item.get("address"), "mailbox")
    if addr.split("@", 1)[1] not in domain_set:
        fail(f"mailbox outside a mail domain: {addr}")
    if addr in mailboxes:
        fail(f"duplicate mailbox: {addr}")
    secret = str(item.get("hash") or "")
    if not HASH.fullmatch(secret):
        fail(f"invalid password hash for {addr}")
    try:
        quota = int(item.get("quota_mb") or 0)
    except (TypeError, ValueError):
        fail(f"invalid quota for {addr}")
    if quota < 0 or quota > 10485760:
        fail(f"invalid quota for {addr}")
    extra = []
    if quota:
        extra.append(f"userdb_quota_rule=*:storage={quota}M")
    if not item.get("enabled", True):
        denied.append(f"{addr}:")
    mailboxes[addr] = True
    passwd.append(f"{addr}:{secret}::::::{' '.join(extra)}")

aliases = {}
for item in data.get("forwarders") or []:
    addr = address(item.get("address"), "forwarder")
    if addr.split("@", 1)[1] not in domain_set:
        fail(f"forwarder outside a mail domain: {addr}")
    targets = [remote(t) for t in (item.get("to") or [])]
    if not targets or len(targets) > 50:
        fail(f"forwarder {addr} needs 1 to 50 destinations")
    if addr in aliases:
        fail(f"duplicate forwarder: {addr}")
    if addr in mailboxes and addr not in targets:
        # Keep a copy in the mailbox: Exim hands an address redirected to
        # itself on to the mailbox router.
        targets.append(addr)
    aliases[addr] = targets


def domain_list(values, owner):
    clean = []
    for value in values or []:
        value = str(value or "").strip().lower()
        if not DOMAIN.fullmatch(value):
            fail(f"invalid sender domain for {owner}: {value!r}")
        if value not in clean:
            clean.append(value)
    return clean


senders = {}
for key, values in (data.get("senders") or {}).items():
    senders[address(key, "sender")] = domain_list(values, key)

local_senders = {}
for key, values in (data.get("local_senders") or {}).items():
    if not LINUX_USER.fullmatch(str(key)):
        fail(f"invalid Linux user: {key!r}")
    local_senders[str(key)] = domain_list(values, key)

dkim = [d for d in domains if (mail_dir / "dkim" / f"{d}.key").is_file()]

RELAY_ID = re.compile(r"^(?:direct|[a-z0-9][a-z0-9-]{0,31})$")
routes = []
for name, value in (data.get("relay_routes") or {}).items():
    name, value = str(name).lower(), str(value or "")
    if name not in domain_set or not RELAY_ID.fullmatch(value):
        fail(f"invalid relay route {name!r}: {value!r}")
    routes.append(f"{name}: {value}")
default_relay = str(data.get("default_relay") or "")
if default_relay:
    if not RELAY_ID.fullmatch(default_relay) or default_relay == "direct":
        fail(f"invalid default relay {default_relay!r}")
    routes.append(f"*: {default_relay}")

exim_gid = grp.getgrnam("Debian-exim").gr_gid
dovecot_gid = grp.getgrnam("dovecot").gr_gid


def write(name, lines, gid):
    path = mail_dir / name
    tmp = mail_dir / f".{name}.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("".join(line + "\n" for line in lines))
    os.chown(tmp, 0, gid)
    os.chmod(tmp, 0o640)
    tmp.replace(path)


write("domains", domains, exim_gid)
write("mailboxes", sorted(mailboxes), exim_gid)
write("aliases", [f"{a}: {', '.join(t)}" for a, t in sorted(aliases.items())], exim_gid)
write("catchall", [f"{d}: {t}" for d, t in sorted(catchall.items())], exim_gid)
write("senders", [f"{a}: {' : '.join(d)}" for a, d in sorted(senders.items()) if d], exim_gid)
write("local_senders", [f"{u}: {' : '.join(d)}" for u, d in sorted(local_senders.items()) if d], exim_gid)
write("dkim_domains", [f"{d}: {d}" for d in dkim], exim_gid)
write("relay_routes", sorted(routes), exim_gid)
previously_denied = set()
try:
    previously_denied = {line.split(":", 1)[0] for line in (mail_dir / "denied").read_text(encoding="utf-8").splitlines() if line}
except OSError:
    pass
write("passwd", sorted(passwd), dovecot_gid)
write("denied", sorted(denied), dovecot_gid)
# A mailbox suspended just now loses the IMAP and POP3 sessions it still has
# open; new sign-ins are refused by the deny passdb.
import subprocess
for line in denied:
    addr = line.split(":", 1)[0]
    if addr not in previously_denied:
        subprocess.run(["doveadm", "kick", addr], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
print(f"mail maps written: {len(domains)} domains, {len(mailboxes)} mailboxes, {len(aliases)} forwarders")
PY
  rm -f -- "$payload"
  return "$rc"
}

mail_dkim_ensure() {
  # Prints the public key (base64, one line) for the DNS record, creating the
  # key pair first when the domain has none -- or always, with "rotate".
  local domain="$1" mode="${2:-}" key tmp
  require_domain "$domain"
  key="${MAIL_DIR}/dkim/${domain}.key"
  if [[ ! -f "$key" || "$mode" == "rotate" ]]; then
    tmp="$(mktemp "${MAIL_DIR}/dkim/.${domain}.XXXXXX")"
    openssl genrsa -out "$tmp" 2048 >/dev/null 2>&1 || { rm -f "$tmp"; deny "could not create a DKIM key"; }
    chown root:Debian-exim "$tmp"
    chmod 0640 "$tmp"
    mv -f "$tmp" "$key"
  fi
  openssl rsa -in "$key" -pubout -outform PEM 2>/dev/null | grep -v '^-----' | tr -d '\n'
  echo
}

mail_purge_mailbox() {
  local addr="$1" local_part domain dir
  require_mail_address "$addr"
  local_part="${addr%@*}"; domain="${addr#*@}"
  dir="${MAIL_VMAIL_DIR}/${domain}/${local_part}"
  [[ "$(readlink -m "$dir")" == "${MAIL_VMAIL_DIR}/${domain}/${local_part}" ]] || deny "refusing an unexpected mailbox path"
  rm -rf -- "${dir:?}"
  echo "mailbox data removed: ${addr}"
}

mail_purge_domain() {
  local domain="$1" dir
  require_domain "$domain"
  dir="${MAIL_VMAIL_DIR}/${domain}"
  [[ "$(readlink -m "$dir")" == "$dir" ]] || deny "refusing an unexpected domain path"
  rm -rf -- "${dir:?}"
  rm -f -- "${MAIL_DIR}/dkim/${domain}.key"
  echo "mail data removed: ${domain}"
}

mail_usage() {
  # "<address> <KiB>" for every maildir that exists.
  local dir domain user size
  [[ -d "$MAIL_VMAIL_DIR" ]] || return 0
  while IFS=$'\t' read -r size dir; do
    user="$(basename "$dir")"; domain="$(basename "$(dirname "$dir")")"
    [[ "$user" =~ ^[a-z0-9._+-]+$ && "$domain" =~ ^[a-z0-9.-]+$ ]] || continue
    printf '%s@%s %s\n' "$user" "$domain" "$size"
  done < <(du -sk --one-file-system "$MAIL_VMAIL_DIR"/*/*/ 2>/dev/null | sed 's:/*$::' || true)
}

mail_restart_services() {
  local unit
  systemctl daemon-reload
  for unit in "${MAIL_SERVICES[@]}"; do
    systemctl enable "$unit" >/dev/null 2>&1 || true
    systemctl restart "$unit" || deny "${unit} failed to start -- check: journalctl -u ${unit}"
  done
}

mail_config_digest() {
  cat /etc/exim4/exim4.conf /etc/dovecot/dovecot.conf /etc/rspamd/local.d/*.conf /etc/rspamd/local.d/*.inc \
    "$MAIL_UNBOUND_CONF" "$MAIL_WEBMAIL_ENV" "$MAIL_WEBMAIL_UNIT" 2>/dev/null | sha256sum | cut -d' ' -f1
}

mail_refresh() {
  # Brings an installed Email addon up to this release: packages added since
  # it was installed, and every generated configuration. update.sh runs it
  # through log-hygiene with the new helper, so a fix reaches a box when its
  # admin presses Update -- nobody has to reinstall the addon.
  mail_installed || return 0
  export DEBIAN_FRONTEND=noninteractive
  local -a missing=()
  local pkg before after old_commit unit
  for pkg in "${MAIL_PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
      missing+=("$pkg")
    fi
  done
  if (( ${#missing[@]} )); then
    apt-get -o DPkg::Lock::Timeout=300 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
      install -y "${missing[@]}" >/dev/null 2>&1 || deny "could not install ${missing[*]}"
  fi
  before="$(mail_config_digest)"
  old_commit="$(git -C "${MAIL_WEBMAIL_ROOT}/src" rev-parse HEAD 2>/dev/null || true)"
  mail_ensure_layout
  mail_tls_sync --no-reload
  mail_write_exim_config
  mail_write_relay_credentials
  mail_write_dovecot_config
  mail_write_sieve
  mail_write_unbound_config
  mail_write_rspamd_config
  mail_webmail_install
  mail_write_webmail_vhost
  after="$(mail_config_digest)"
  if [[ "$before" != "$after" || ${#missing[@]} -gt 0 ]]; then
    systemctl daemon-reload
    for unit in unbound rspamd dovecot exim4; do
      systemctl enable "$unit" >/dev/null 2>&1 || true
      systemctl restart "$unit" >/dev/null 2>&1 || echo "WARNING: ${unit} did not restart" >&2
    done
  fi
  if [[ "$old_commit" != "$MAIL_WEBMAIL_COMMIT" || "$before" != "$after" ]]; then
    systemctl restart bnix-webmail >/dev/null 2>&1 || echo "WARNING: bnix-webmail did not restart" >&2
  fi
  echo "Email addon configuration refreshed"
}

addon_mail_status() {
  local installed=0 running=0 enabled=0 version="" unit down=""
  if mail_installed && command -v exim4 >/dev/null 2>&1 && command -v doveconf >/dev/null 2>&1; then
    installed=1
  fi
  if [[ $installed -eq 1 ]]; then
    running=1; enabled=1
    for unit in "${MAIL_SERVICES[@]}"; do
      if ! systemctl is-active --quiet "$unit" 2>/dev/null; then
        down="${down:+${down},}${unit}"
        case "$unit" in
          exim4|dovecot) running=0 ;;
        esac
      fi
      systemctl is-enabled --quiet "$unit" 2>/dev/null || enabled=0
    done
    version="$(exim4 -bV 2>/dev/null | sed -n 's/^Exim version \([0-9.]*\).*/\1/p' | head -n 1 || true)"
  fi
  echo "installed=${installed} running=${running} enabled=${enabled} version=${version} down=${down}"
}

addon_mail_install() {
  export DEBIAN_FRONTEND=noninteractive
  local pkg created_aliases=0 created_mailname=0
  # Exim's package writes these when they are missing; Remove takes them away
  # again only if this install is what created them.
  [[ -e /etc/aliases ]] || created_aliases=1
  [[ -e /etc/mailname ]] || created_mailname=1
  # Exim replaces any other mail server apt knows about, silently. Refuse
  # instead: whatever the admin set up is theirs to remove.
  for pkg in postfix sendmail-bin opensmtpd; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
      deny "${pkg} is installed. Remove it first -- the Email addon brings its own mail server (Exim)."
    fi
  done
  apt-get update --allow-releaseinfo-change >/dev/null 2>&1 || true
  apt-get -o DPkg::Lock::Timeout=300 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
    install -y "${MAIL_PACKAGES[@]}" >/dev/null || deny "apt-get install of Exim, Dovecot and Rspamd failed"
  command -v git >/dev/null 2>&1 || apt-get -o DPkg::Lock::Timeout=300 install -y git >/dev/null \
    || deny "apt-get install git failed"
  mail_ensure_layout
  if [[ $created_aliases -eq 1 ]]; then touch "${MAIL_DIR}/.created-aliases"; fi
  if [[ $created_mailname -eq 1 ]]; then touch "${MAIL_DIR}/.created-mailname"; fi
  mail_tls_sync --force --no-reload
  mail_write_exim_config
  mail_write_relay_credentials
  mail_write_dovecot_config
  mail_write_sieve
  mail_write_unbound_config
  mail_write_rspamd_config
  mail_webmail_install
  mail_write_webmail_vhost
  touch "$MAIL_MARKER"
  chmod 0644 "$MAIL_MARKER"
  mail_restart_services
  ols_sync_main_config
  restart_openlitespeed
  mail_open_ports
  echo "Email installed: Exim, Dovecot, Rspamd and the webmail on port ${MAIL_WEBMAIL_PUBLIC_PORT}"
}

addon_mail_uninstall() {
  export DEBIAN_FRONTEND=noninteractive
  local installed=() pkg name ok host
  systemctl disable --now bnix-webmail >/dev/null 2>&1 || true
  rm -f "$MAIL_WEBMAIL_UNIT"
  systemctl daemon-reload
  rm -rf -- "${MAIL_WEBMAIL_ROOT:?}" "$MAIL_WEBMAIL_ENV" "$MAIL_SSO_SECRET_FILE" "${MAIL_DIR}/master-users"
  if [[ -f "$MAIL_WEBMAIL_HOSTS" ]]; then
    while IFS= read -r host; do
      [[ -n "$host" ]] && is_domain "$host" && [[ "$host" == webmail.* ]] || continue
      mail_webmail_host_remove "$host"
    done < <(cat "$MAIL_WEBMAIL_HOSTS")
  fi
  # OpenLiteSpeed keeps its own copies beside a vhost file it loaded.
  rm -f "$MAIL_WEBMAIL_VHOST" "${MAIL_WEBMAIL_VHOST}.txt" "${MAIL_WEBMAIL_VHOST}0" "${MAIL_WEBMAIL_VHOST}0,v"
  ols_sync_main_config
  restart_openlitespeed || true
  mail_close_ports
  systemctl disable --now exim4 dovecot rspamd unbound >/dev/null 2>&1 || true
  for pkg in "${MAIL_REMOVABLE_PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
      installed+=("$pkg")
    fi
  done
  if (( ${#installed[@]} )); then
    for name in $(apt-get -s purge "${installed[@]}" 2>/dev/null | awk '/^(Purg|Remv) /{print $2}'); do
      ok=0
      for pkg in "${MAIL_REMOVABLE_PACKAGES[@]}"; do [[ "$name" == "$pkg" ]] && ok=1; done
      # apt pulled it in with the mail server (bsd-mailx comes with Exim) and
      # nobody installed it by name, so it goes too. A package the admin
      # installed still stops the removal.
      if [[ $ok -eq 0 && -n "$(apt-mark showauto "$name" 2>/dev/null)" ]]; then ok=1; fi
      [[ $ok -eq 1 ]] || deny "removing the mail server would also remove ${name}, which was installed separately"
    done
    apt-get -o DPkg::Lock::Timeout=120 purge -y "${installed[@]}" >/dev/null || deny "apt could not remove the mail server"
  fi
  rm -f /etc/exim4/exim4.conf "$MAIL_MARKER" "${MAIL_DIR}/relay.user" "${MAIL_DIR}/relay.pass"
  if [[ -f "${MAIL_DIR}/.created-aliases" ]]; then rm -f /etc/aliases "${MAIL_DIR}/.created-aliases"; fi
  if [[ -f "${MAIL_DIR}/.created-mailname" ]]; then rm -f /etc/mailname "${MAIL_DIR}/.created-mailname"; fi
  rm -rf -- "${MAIL_WEBMAIL_DOCROOT:?}"
  rmdir /etc/dovecot/conf.d /etc/dovecot /etc/exim4 2>/dev/null || true
  # The webmail's user owns nothing any more; vmail stays with the mailboxes.
  if id bnix-webmail >/dev/null 2>&1; then userdel bnix-webmail >/dev/null 2>&1 || true; fi
  if getent group bnix-webmail >/dev/null 2>&1; then groupdel bnix-webmail >/dev/null 2>&1 || true; fi
  rm -rf -- "${MAIL_DIR:?}/tls"
  rm -f /etc/rspamd/local.d/actions.conf /etc/rspamd/local.d/redis.conf /etc/rspamd/local.d/classifier-bayes.conf \
    /etc/rspamd/local.d/dkim_signing.conf /etc/rspamd/local.d/arc.conf /etc/rspamd/local.d/logging.inc \
    /etc/rspamd/local.d/options.inc "$MAIL_UNBOUND_CONF"
  systemctl unmask unbound-resolvconf.service >/dev/null 2>&1 || true
  echo "Email removed. Mailboxes in ${MAIL_VMAIL_DIR} and the DKIM keys are kept."
}

addon_mail_enable() {
  require_mail_installed
  mail_restart_services
  echo "Email started"
}

addon_mail_disable() {
  local unit
  for unit in bnix-webmail exim4 dovecot rspamd unbound; do
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
  done
  echo "Email stopped"
}

# --- DNS Manager: PowerDNS ---------------------------------------------------
#
# An authoritative nameserver for the customers' zones. The panel owns who
# owns which zone; the records themselves live in PowerDNS, which the panel
# edits through its HTTP API on 127.0.0.1 (the key is in a file only the
# panel user and root can read). Nothing here runs until the addon is
# installed: every path is guarded by the marker.
DNS_DIR="/etc/opanel-dns"
DNS_MARKER="${DNS_DIR}/installed"
DNS_DATA_DIR="/var/lib/opanel-dns"
DNS_DB="${DNS_DATA_DIR}/pdns.sqlite3"
DNS_CONF="/etc/powerdns/pdns.conf"
DNS_PACKAGES=(pdns-server pdns-backend-sqlite3 sqlite3)
DNS_REMOVABLE_PACKAGES=(pdns-server pdns-backend-sqlite3 pdns-backend-bind)
DNS_API_PORT="8081"
DNS_ROOT_KEY_FILE="${DNS_DIR}/api.key"
DNS_PANEL_KEY_FILE="${opanel_DATA_DIR}/addons/dns-api.key"
DNS_ACME_HOOK="/usr/local/sbin/opanel-dns-acme-hook"

dns_installed() {
  [[ -f "$DNS_MARKER" ]]
}

require_dns_installed() {
  dns_installed || deny "the DNS Manager addon is not installed"
}

dns_listen_addresses() {
  # Every global address, never the wildcard: systemd-resolved already holds
  # 127.0.0.53:53, and a bind to 0.0.0.0:53 fails beside it.
  python3 - <<'PY'
import ipaddress
import subprocess

found = []
text = subprocess.run(["ip", "-o", "addr", "show", "scope", "global"], capture_output=True, text=True).stdout
for line in text.splitlines():
    parts = line.split()
    for family in ("inet", "inet6"):
        if family in parts:
            address = parts[parts.index(family) + 1].split("/")[0]
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if not (ip.is_loopback or ip.is_link_local) and address not in found:
                found.append(address)
print(", ".join(found))
PY
}

dns_ensure_keys() {
  install -d -o root -g root -m 0755 "$DNS_DIR"
  if [[ ! -s "$DNS_ROOT_KEY_FILE" ]]; then
    openssl rand -hex 32 | mail_write_file "$DNS_ROOT_KEY_FILE" root:root 0600
  fi
  install -d -o opanel -g opanel -m 0750 "${opanel_DATA_DIR}/addons"
  mail_write_file "$DNS_PANEL_KEY_FILE" opanel:opanel 0600 <"$DNS_ROOT_KEY_FILE"
}

dns_init_db() {
  local schema
  install -d -o pdns -g pdns -m 0750 "$DNS_DATA_DIR"
  # Only ever created, never replaced: an existing database holds the zones.
  if [[ ! -s "$DNS_DB" ]]; then
    # The full schema, not one of the N_to_M upgrade scripts beside it.
    schema="$(dpkg -L pdns-backend-sqlite3 2>/dev/null | grep -E '/schema\.sqlite3\.sql(\.gz)?$' | sort | head -n 1 || true)"
    [[ -n "$schema" && -f "$schema" ]] || deny "the PowerDNS SQLite schema was not found"
    rm -f "${DNS_DB}.new"
    if [[ "$schema" == *.gz ]]; then
      zcat "$schema" | sqlite3 "${DNS_DB}.new" || deny "could not create the DNS database"
    else
      sqlite3 "${DNS_DB}.new" <"$schema" || deny "could not create the DNS database"
    fi
    sqlite3 "${DNS_DB}.new" "select 1 from records limit 1" >/dev/null || deny "the DNS database has no records table"
    mv -f "${DNS_DB}.new" "$DNS_DB"
  fi
  chown pdns:pdns "$DNS_DB"
  chmod 0640 "$DNS_DB"
}

dns_write_config() {
  local key addresses
  key="$(cat "$DNS_ROOT_KEY_FILE")"
  addresses="$(dns_listen_addresses)"
  [[ -n "$addresses" ]] || deny "this server has no public address to answer DNS on"
  install -d -o root -g root -m 0755 /etc/powerdns
  mail_write_file "${DNS_CONF}.new" root:pdns 0640 <<CONF
# Managed by OPanel (DNS Manager addon). Rewritten on install and on every
# panel update; the package's pdns.d is not read.
launch=gsqlite3
gsqlite3-database=${DNS_DB}
gsqlite3-dnssec=no
local-address=${addresses}
local-port=53
api=yes
api-key=${key}
webserver=yes
webserver-address=127.0.0.1
webserver-port=${DNS_API_PORT}
webserver-allow-from=127.0.0.1,::1
disable-axfr=yes
version-string=anonymous
default-ttl=3600
setuid=pdns
setgid=pdns
security-poll-suffix=
CONF
  mv -f "${DNS_CONF}.new" "$DNS_CONF"
}

dns_write_acme_hook() {
  # certbot --manual hooks: publish the DNS-01 challenge in this server's own
  # zone, and take it away again. Both challenges of a domain + *.domain
  # certificate sit at the same name, so values are added, not replaced.
  mail_write_file "$DNS_ACME_HOOK" root:root 0700 <<'HOOK'
#!/usr/bin/env python3
# Managed by OPanel (DNS Manager addon).
import json
import os
import sys
import time
import urllib.error
import urllib.request

KEY = open("/etc/opanel-dns/api.key", encoding="utf-8").read().strip()
BASE = "http://127.0.0.1:8081/api/v1/servers/localhost/zones/"


def call(method, path, body=None):
    request = urllib.request.Request(BASE + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        text = response.read()
        return json.loads(text) if text else None


def zone_for(name):
    labels = name.rstrip(".").split(".")
    for index in range(len(labels) - 1):
        candidate = ".".join(labels[index:]) + "."
        try:
            return candidate, call("GET", candidate)
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 422):
                raise
    sys.exit(f"no zone on this server for {name}")


def main():
    action = sys.argv[1]
    domain = os.environ["CERTBOT_DOMAIN"].rstrip(".").lower()
    value = '"%s"' % os.environ["CERTBOT_VALIDATION"]
    name = f"_acme-challenge.{domain}."
    zone, data = zone_for(domain)
    current = [record["content"] for rrset in data.get("rrsets", [])
               if rrset["name"] == name and rrset["type"] == "TXT" for record in rrset["records"]]
    values = [v for v in current if v != value] + ([value] if action == "auth" else [])
    rrset = {"name": name, "type": "TXT", "ttl": 60}
    if values:
        rrset.update(changetype="REPLACE", records=[{"content": v, "disabled": False} for v in values])
    else:
        rrset.update(changetype="DELETE", records=[])
    call("PATCH", zone, {"rrsets": [rrset]})
    if action == "auth":
        time.sleep(3)


main()
HOOK
}

dns_open_ports() {
  # UDP and TCP 53, both families, as panel rules.
  local binary proto
  iptables_ensure_opanel_chains 2>/dev/null || true
  for binary in iptables ip6tables; do
    for proto in udp tcp; do
      while "$binary" -D OPANEL_INPUT -p "$proto" --dport 53 -j ACCEPT -m comment --comment "opanel:PanelZone" 2>/dev/null; do :; done
      "$binary" -I OPANEL_INPUT 1 -p "$proto" --dport 53 -j ACCEPT -m comment --comment "opanel:PanelZone" 2>/dev/null || true
    done
  done
}

dns_close_ports() {
  local binary proto
  for binary in iptables ip6tables; do
    for proto in udp tcp; do
      while "$binary" -D OPANEL_INPUT -p "$proto" --dport 53 -j ACCEPT -m comment --comment "opanel:PanelZone" 2>/dev/null; do :; done
    done
  done
}

dns_wait_for_api() {
  local attempt
  for attempt in $(seq 1 20); do
    if curl -fsS -m 2 -H "X-API-Key: $(cat "$DNS_ROOT_KEY_FILE")" \
        "http://127.0.0.1:${DNS_API_PORT}/api/v1/servers/localhost" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  deny "PowerDNS did not answer on its API -- check: journalctl -u pdns"
}

addon_dns_status() {
  local installed=0 running=0 enabled=0 version=""
  if dns_installed && command -v pdns_server >/dev/null 2>&1; then
    installed=1
    systemctl is-active --quiet pdns 2>/dev/null && running=1
    systemctl is-enabled --quiet pdns 2>/dev/null && enabled=1
    version="$(dpkg-query -W -f='${Version}' pdns-server 2>/dev/null | sed 's/-.*//' || true)"
  fi
  echo "installed=${installed} running=${running} enabled=${enabled} version=${version}"
}

addon_dns_install() {
  export DEBIAN_FRONTEND=noninteractive
  local pkg
  # Another DNS server on this box would fight over port 53.
  for pkg in bind9 dnsmasq; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
      deny "${pkg} is installed. Remove it first -- DNS Manager runs its own nameserver (PowerDNS)."
    fi
  done
  apt-get update --allow-releaseinfo-change >/dev/null 2>&1 || true
  # The package would start PowerDNS on its own config (BIND backend, every
  # address), which can fail beside systemd-resolved and fail the install:
  # keep it stopped until ours is written.
  local guard="/usr/sbin/policy-rc.d" guarded=0 rc=0
  if [[ ! -e "$guard" ]]; then
    printf '#!/bin/sh\nexit 101\n' >"$guard"
    chmod 0755 "$guard"
    guarded=1
  fi
  if apt-get -o DPkg::Lock::Timeout=300 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
      install -y "${DNS_PACKAGES[@]}" >/dev/null; then rc=0; else rc=$?; fi
  [[ $guarded -eq 1 ]] && rm -f "$guard"
  [[ $rc -eq 0 ]] || deny "apt-get install of PowerDNS failed"
  systemctl stop pdns >/dev/null 2>&1 || true
  dns_ensure_keys
  dns_init_db
  dns_write_config
  dns_write_acme_hook
  touch "$DNS_MARKER"
  chmod 0644 "$DNS_MARKER"
  systemctl enable pdns >/dev/null 2>&1 || true
  systemctl restart pdns || deny "PowerDNS failed to start -- check: journalctl -u pdns"
  dns_wait_for_api
  dns_open_ports
  iptables_restore_addon_precedence 2>/dev/null || true
  firewall_persist_rules
  echo "DNS Manager installed: PowerDNS answers on port 53"
}

addon_dns_uninstall() {
  export DEBIAN_FRONTEND=noninteractive
  local installed=() pkg name ok
  systemctl disable --now pdns >/dev/null 2>&1 || true
  dns_close_ports
  firewall_persist_rules
  for pkg in "${DNS_REMOVABLE_PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
      installed+=("$pkg")
    fi
  done
  if (( ${#installed[@]} )); then
    for name in $(apt-get -s purge "${installed[@]}" 2>/dev/null | awk '/^(Purg|Remv) /{print $2}'); do
      ok=0
      for pkg in "${DNS_REMOVABLE_PACKAGES[@]}"; do [[ "$name" == "$pkg" ]] && ok=1; done
      if [[ $ok -eq 0 && -n "$(apt-mark showauto "$name" 2>/dev/null)" ]]; then ok=1; fi
      [[ $ok -eq 1 ]] || deny "removing PowerDNS would also remove ${name}, which was installed separately"
    done
    apt-get -o DPkg::Lock::Timeout=120 purge -y "${installed[@]}" >/dev/null || deny "apt could not remove PowerDNS"
  fi
  rm -f "$DNS_MARKER" "$DNS_ACME_HOOK" "$DNS_PANEL_KEY_FILE" "$DNS_CONF" "${DNS_CONF}.new"
  rmdir /etc/powerdns/pdns.d /etc/powerdns 2>/dev/null || true
  echo "DNS Manager removed. The zones in ${DNS_DATA_DIR} are kept."
}

addon_dns_enable() {
  require_dns_installed
  systemctl enable pdns >/dev/null 2>&1 || true
  systemctl restart pdns || deny "PowerDNS failed to start -- check: journalctl -u pdns"
  dns_wait_for_api
  echo "DNS Manager started"
}

addon_dns_disable() {
  systemctl disable --now pdns >/dev/null 2>&1 || true
  echo "DNS Manager stopped"
}

dns_refresh() {
  # Brings an installed DNS Manager up to this release (run by log-hygiene on
  # every update): new packages, the configuration -- the server's addresses
  # may have changed -- and the ACME hook. Restarts only if something did.
  dns_installed || return 0
  export DEBIAN_FRONTEND=noninteractive
  local -a missing=()
  local pkg before after
  for pkg in "${DNS_PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
      missing+=("$pkg")
    fi
  done
  if (( ${#missing[@]} )); then
    apt-get -o DPkg::Lock::Timeout=300 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
      install -y "${missing[@]}" >/dev/null 2>&1 || deny "could not install ${missing[*]}"
  fi
  before="$(sha256sum "$DNS_CONF" 2>/dev/null | cut -d' ' -f1 || true)"
  dns_ensure_keys
  dns_init_db
  dns_write_config
  dns_write_acme_hook
  after="$(sha256sum "$DNS_CONF" | cut -d' ' -f1)"
  if [[ "$before" != "$after" ]] && systemctl is-enabled --quiet pdns 2>/dev/null; then
    systemctl restart pdns >/dev/null 2>&1 || echo "WARNING: pdns did not restart" >&2
  fi
  echo "DNS Manager configuration refreshed"
}

issue_local_dns_wildcard() {
  # A wildcard certificate over DNS-01 in this server's own zone (see
  # dns_write_acme_hook). certbot keeps the hooks for its renewals.
  local domain="$1" email="${2:-}"
  require_dns_installed
  require_domain "$domain"
  [[ -x "$DNS_ACME_HOOK" ]] || deny "the DNS challenge hook is missing; update the panel"
  local args=(certonly --manual --preferred-challenges dns
    --manual-auth-hook "$DNS_ACME_HOOK auth" --manual-cleanup-hook "$DNS_ACME_HOOK cleanup"
    --cert-name "$domain" --non-interactive --agree-tos --expand
    --deploy-hook "systemctl restart lshttpd.service 2>/dev/null || /usr/local/lsws/bin/lswsctrl restart 2>/dev/null || true; systemctl restart opanel-api 2>/dev/null || true"
    -d "$domain" -d "*.${domain}")
  if [[ -n "$email" ]]; then
    require_email "$email"
    args+=(--email "$email")
  else
    args+=(--register-unsafely-without-email)
  fi
  certbot "${args[@]}" || deny "Let's Encrypt could not complete the DNS challenge for ${domain}. Its nameservers must be this server's."
  restart_openlitespeed
  copy_panel_live_certificate "$domain"
  panel_cert_store_sync
  echo "Wildcard SSL certificate issued for ${domain} (and *.${domain}) with this server's DNS"
}

# --- Keeping addon firewall rules effective ---------------------------------
addon_fail2ban_wait_for_chains() {
  # systemctl returns once the server is up, which is before its jails have
  # inserted anything: measured on the test box, the chains appear about two
  # seconds later. Repositioning an empty chain set leaves each jump wherever
  # fail2ban puts it afterwards, which is the top of INPUT -- ahead of the
  # admin rules.
  #
  # The jail count has to be read only once the server answers. The first
  # version read it immediately, got nothing back, took that for "no jails"
  # and returned without waiting at all.
  local want have attempt
  want=0
  for attempt in $(seq 1 20); do
    if fail2ban-client ping >/dev/null 2>&1; then
      want="$(fail2ban-client status 2>/dev/null         | sed -n 's/.*Number of jail:[[:space:]]*//p' | head -n 1 | tr -d '[:space:]')"
      if [[ "$want" =~ ^[0-9]+$ ]] && [[ "$want" -gt 0 ]]; then break; fi
      want=0
    fi
    sleep 1
  done
  if [[ "$want" -eq 0 ]]; then return 0; fi

  for attempt in $(seq 1 20); do
    have="$(iptables -S 2>/dev/null | grep -c '^-N f2b-' || true)"
    if [[ ! "$have" =~ ^[0-9]+$ ]]; then have=0; fi
    if [[ "$have" -ge "$want" ]]; then return 0; fi
    sleep 1
  done
  # Not fatal: the next firewall change reasserts the order anyway.
  return 0
}

iptables_restore_addon_precedence() {
  # OPANEL_INPUT accepts the default ports (22/80/443/the panel) from any
  # source, and an ACCEPT inside a user chain ends traversal of INPUT. A f2b
  # jump below it therefore never runs: fail2ban goes on listing the ban while
  # the packets have already been accepted -- a control that reports success
  # and does nothing. iptables_reorder_managed_jumps re-inserts the panel's
  # three jumps at 1/2/3 on every firewall change, which is what pushes f2b
  # down, so this runs after it.
  #
  # Final order is BLOCKLIST, USER, f2b..., OPANEL_INPUT: an admin's explicit
  # rule outranks an automatic ban, and an automatic ban outranks a blanket
  # default allow.
  #
  # Each rule is moved by its exact specification. Deleting by target alone
  # missed fail2ban's own "-p tcp -j f2b-sshd" and left a second, bare jump
  # beside it -- dead duplicates below OPANEL_INPUT, and worse, a reference
  # that stopped fail2ban from removing its own chain on shutdown, so bans
  # outlived the addon with nothing left able to lift them.
  local binary target spec chain
  for binary in iptables ip6tables; do
    local -a specs=()
    while IFS= read -r spec; do
      if [[ -n "$spec" ]]; then specs+=("$spec"); fi
    done < <("$binary" -S INPUT 2>/dev/null \
      | grep -E -- ' -j f2b-[A-Za-z0-9_.-]+$' || true)
    if [[ ${#specs[@]} -eq 0 ]]; then continue; fi

    # One jump per chain. A box upgraded from the version that added bare
    # duplicates has two rules per chain; keep the more specific one, which is
    # the one fail2ban itself tracks and will expect to delete later.
    local -A keep=()
    local -a parts=()
    for spec in "${specs[@]}"; do
      read -ra parts <<<"$spec"
      chain="${parts[${#parts[@]}-1]}"
      if [[ -z "${keep[$chain]:-}" || ${#spec} -gt ${#keep[$chain]} ]]; then
        keep["$chain"]="$spec"
      fi
    done

    for spec in "${specs[@]}"; do
      read -ra parts <<<"$spec"
      "$binary" -D INPUT "${parts[@]:2}" 2>/dev/null || true
    done

    target="$("$binary" -L INPUT -n --line-numbers 2>/dev/null \
      | awk '$2 == "OPANEL_INPUT" { print $1; exit }' || true)"
    if [[ ! "$target" =~ ^[0-9]+$ ]]; then target=1; fi

    for chain in "${!keep[@]}"; do
      read -ra parts <<<"${keep[$chain]}"
      "$binary" -I INPUT "$target" "${parts[@]:2}" 2>/dev/null || true
    done
  done
}


# ---------------------------------------------------------------------------
# Extra SFTP accounts ("SFTP sub-accounts")
#
# A hosting account's own SFTP login is its Linux user, jailed in /home/<user>
# (see setup_sftp_access in install.sh). A sub-account is a second login for
# one folder of that account, with its own password:
#
#   * a Linux user <owner>_<suffix> sharing the owner's uid and primary group
#     (useradd -o), so what it uploads is owned exactly like the owner's own
#     files -- the site's PHP can read and write them, and they count against
#     the owner's quota;
#   * jailed by sshd in /var/lib/opanel-sftp/<sub> (root:<owner group> 0750,
#     which is what ChrootDirectory requires and what keeps other tenants from
#     walking in), holding one bind mount of the chosen folder;
#   * internal-sftp only: no shell, no forwarding.
#
# The mounts are listed in a root-only state file and put back at boot by a
# small standalone script (the helper refuses anything not invoked through
# sudo by opanel, so a boot unit cannot call it).
# ---------------------------------------------------------------------------
opanel_SFTP_SUB_GROUP="opanel-sftp-sub"
SFTP_JAIL_ROOT="/var/lib/opanel-sftp"
SFTP_SUB_STATE="${SFTP_JAIL_ROOT}/.accounts"
SFTP_MOUNT_SCRIPT="/usr/local/sbin/opanel-sftp-mounts"
SFTP_MOUNT_UNIT="/etc/systemd/system/opanel-sftp-mounts.service"

require_sftp_sub_name() {
  local owner="$1" sub="$2"
  require_linux_user "$owner"
  [[ "$sub" =~ ^[a-z_][a-z0-9_-]{2,31}$ ]] || deny "invalid SFTP account name: $sub"
  [[ "$sub" == "${owner}_"?* ]] || deny "SFTP account name must start with ${owner}_"
  [[ "$sub" != "$owner" ]] || deny "SFTP account name must differ from the account"
}

sftp_sub_owner_of() {
  # Prints the owner recorded for <sub>, or nothing.
  [[ -f "$SFTP_SUB_STATE" ]] || return 0
  awk -v s="$1" '$1 == s { print $2; exit }' "$SFTP_SUB_STATE"
}

sftp_sub_mountpoint_of() {
  [[ -f "$SFTP_SUB_STATE" ]] || return 0
  awk -v s="$1" '$1 == s { print $4; exit }' "$SFTP_SUB_STATE"
}

reload_sshd_service() {
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
}

ensure_sftp_sub_runtime() {
  command -v sshd >/dev/null 2>&1 || deny "OpenSSH server is not installed"
  getent group "$opanel_SFTP_SUB_GROUP" >/dev/null || groupadd --system "$opanel_SFTP_SUB_GROUP"
  install -d -o root -g root -m 0711 "$SFTP_JAIL_ROOT"
  [[ -f "$SFTP_SUB_STATE" ]] || install -o root -g root -m 0600 /dev/null "$SFTP_SUB_STATE"

  local cfg="/etc/ssh/sshd_config" backup
  if ! grep -q '^# BEGIN opanel SFTP SUBACCOUNTS$' "$cfg"; then
    backup="$(mktemp)"
    cp -p "$cfg" "$backup"
    cat >>"$cfg" <<'SSHD'
# BEGIN opanel SFTP SUBACCOUNTS
# Extra SFTP logins for one folder of a hosting account. Each is jailed in
# /var/lib/opanel-sftp/<name>, which holds a bind mount of that folder.
Match Group opanel-sftp-sub
    PasswordAuthentication yes
    ChrootDirectory /var/lib/opanel-sftp/%u
    ForceCommand internal-sftp -d /
    PermitTTY no
    X11Forwarding no
    AllowTcpForwarding no
    PermitTunnel no
# END opanel SFTP SUBACCOUNTS
SSHD
    if sshd -t >/dev/null 2>&1; then
      reload_sshd_service
      rm -f "$backup"
    else
      cp -p "$backup" "$cfg"
      rm -f "$backup"
      deny "sshd rejected the SFTP account block; sshd_config was left as it was"
    fi
  fi

  cat >"$SFTP_MOUNT_SCRIPT" <<'MOUNTS'
#!/usr/bin/env bash
# Written by opanel-helper. Restores the bind mounts behind OPanel's extra SFTP
# accounts after a reboot; every line of the state file is re-checked first.
set -u
STATE=/var/lib/opanel-sftp/.accounts
[[ -f "$STATE" ]] || exit 0
while read -r sub owner src mp; do
  [[ "$sub" =~ ^[a-z_][a-z0-9_-]{2,31}$ && "$owner" =~ ^[a-z_][a-z0-9_-]{2,31}$ ]] || continue
  [[ "$src" == "/home/$owner" || "$src" == "/home/$owner/"* ]] || continue
  [[ "$mp" == "/var/lib/opanel-sftp/$sub/"* ]] || continue
  [[ -d "$src" && -d "$mp" ]] || continue
  [[ "$(readlink -e -- "$src")" == "$src" ]] || continue
  mountpoint -q "$mp" && continue
  mount --bind "$src" "$mp" && mount -o remount,bind,nosuid,nodev "$mp"
done < "$STATE"
exit 0
MOUNTS
  chown root:root "$SFTP_MOUNT_SCRIPT"
  chmod 0755 "$SFTP_MOUNT_SCRIPT"
  cat >"$SFTP_MOUNT_UNIT" <<UNIT
[Unit]
Description=OPanel extra SFTP account folders
After=local-fs.target
Before=ssh.service sshd.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=${SFTP_MOUNT_SCRIPT}

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload 2>/dev/null || true
  systemctl enable opanel-sftp-mounts.service >/dev/null 2>&1 || true
}

sftp_sub_create() {
  local owner="$1" sub="$2" target="$3" password resolved uid gid jail name mp
  require_sftp_sub_name "$owner" "$sub"
  id -u "$owner" >/dev/null 2>&1 || deny "hosting account does not exist: $owner"
  [[ " $(id -nG "$owner") " == *" $opanel_SFTP_GROUP "* ]] || deny "$owner is not a hosting account"
  if id -u "$sub" >/dev/null 2>&1 || getent group "$sub" >/dev/null 2>&1; then
    deny "the name $sub is already taken on this server"
  fi
  [[ "$target" == /* ]] || deny "folder must be an absolute path"
  [[ "$target" =~ ^[A-Za-z0-9._/-]+$ ]] || deny "folder may only contain letters, digits and . _ / -"
  resolved="$(readlink -e -- "$target")" || deny "folder does not exist: $target"
  [[ "$resolved" == "$target" ]] || deny "folder must not go through a symlink"
  [[ -d "$resolved" ]] || deny "not a folder: $target"
  [[ "$resolved" == "$HOME_ROOT/$owner" || "$resolved" == "$HOME_ROOT/$owner/"* ]] \
    || deny "folder must be inside $HOME_ROOT/$owner"

  password="$(cat)"
  password="${password%$'\n'}"
  [[ ${#password} -ge 12 && ${#password} -le 72 ]] || deny "password must be 12-72 characters"
  case "$password" in
    *:*|*$'\r'*|*$'\n'*) deny "password cannot contain ':', carriage returns or newlines" ;;
  esac

  ensure_sftp_sub_runtime
  uid="$(id -u "$owner")"
  gid="$(id -g "$owner")"
  jail="$SFTP_JAIL_ROOT/$sub"
  [[ ! -e "$jail" ]] || deny "a jail for $sub already exists"
  if [[ "$resolved" == "$HOME_ROOT/$owner" ]]; then name="home"; else name="$(basename -- "$resolved")"; fi
  mp="$jail/$name"

  install -d -o root -g "$gid" -m 0750 "$jail"
  install -d -o root -g "$gid" -m 0750 "$mp"
  if ! useradd -o -u "$uid" -g "$gid" -G "$opanel_SFTP_SUB_GROUP" -d "$jail" -M -s /usr/sbin/nologin "$sub"; then
    rmdir "$mp" "$jail" 2>/dev/null || true
    deny "could not create the Linux user $sub"
  fi
  printf '%s:%s\n' "$sub" "$password" | chpasswd
  if ! mount --bind "$resolved" "$mp" || ! mount -o remount,bind,nosuid,nodev "$mp"; then
    mountpoint -q "$mp" && umount "$mp" 2>/dev/null
    if ! mountpoint -q "$mp"; then
      userdel -f "$sub" 2>/dev/null || true
      rmdir "$mp" "$jail" 2>/dev/null || true
    fi
    deny "could not attach $target to the SFTP jail"
  fi
  printf '%s %s %s %s\n' "$sub" "$owner" "$resolved" "$mp" >>"$SFTP_SUB_STATE"
  chmod 0600 "$SFTP_SUB_STATE"
  echo "$sub"
}

sftp_sub_password() {
  local owner="$1" sub="$2" password
  require_sftp_sub_name "$owner" "$sub"
  [[ "$(sftp_sub_owner_of "$sub")" == "$owner" ]] || deny "$sub is not an SFTP account of $owner"
  id -u "$sub" >/dev/null 2>&1 || deny "Linux user $sub does not exist"
  password="$(cat)"
  password="${password%$'\n'}"
  [[ ${#password} -ge 12 && ${#password} -le 72 ]] || deny "password must be 12-72 characters"
  case "$password" in
    *:*|*$'\r'*|*$'\n'*) deny "password cannot contain ':', carriage returns or newlines" ;;
  esac
  printf '%s:%s\n' "$sub" "$password" | chpasswd
}

sftp_sub_delete() {
  local owner="$1" sub="$2" mp jail
  require_sftp_sub_name "$owner" "$sub"
  local recorded
  recorded="$(sftp_sub_owner_of "$sub")"
  [[ -z "$recorded" || "$recorded" == "$owner" ]] || deny "$sub is not an SFTP account of $owner"
  jail="$SFTP_JAIL_ROOT/$sub"
  mp="$(sftp_sub_mountpoint_of "$sub")"
  # End this login's sessions only. They run under the owner's uid, so
  # pkill -u would take the owner's PHP down with them.
  pkill -f "^sshd(-session)?: ${sub}(@| \[)" 2>/dev/null || true
  # The folder must be detached before anything is removed: the jail holds a
  # live mount of the website.
  # find, not a glob: "$jail"/* skips a folder whose name starts with a dot.
  local m
  while IFS= read -r m; do
    [[ -n "$m" && -d "$m" ]] || continue
    if mountpoint -q "$m"; then
      umount "$m" 2>/dev/null || umount -l "$m" 2>/dev/null || true
      mountpoint -q "$m" && deny "could not detach $m; $sub was left in place"
    fi
  done < <(printf '%s\n' "$mp"; [[ -d "$jail" ]] && find "$jail" -mindepth 1 -maxdepth 1 -type d)
  if id -u "$sub" >/dev/null 2>&1; then
    # -f: the shared uid is always "in use" by the owner's processes. Never -r.
    userdel -f "$sub" 2>/dev/null || deny "could not remove the Linux user $sub"
  fi
  if [[ -d "$jail" ]]; then
    while IFS= read -r m; do
      mountpoint -q "$m" && deny "a folder is still attached under $jail"
      rmdir "$m" 2>/dev/null || true
    done < <(find "$jail" -mindepth 1 -maxdepth 1 -type d)
    rmdir "$jail" 2>/dev/null || true
  fi
  if [[ -f "$SFTP_SUB_STATE" ]]; then
    local tmp
    tmp="$(mktemp "${SFTP_JAIL_ROOT}/.accounts.XXXXXX")"
    awk -v s="$sub" '$1 != s' "$SFTP_SUB_STATE" >"$tmp"
    chmod 0600 "$tmp"
    mv -f "$tmp" "$SFTP_SUB_STATE"
  fi
}

sftp_sub_delete_all_for_owner() {
  local owner="$1" sub
  require_linux_user "$owner"
  [[ -f "$SFTP_SUB_STATE" ]] || return 0
  for sub in $(awk -v o="$owner" '$2 == o { print $1 }' "$SFTP_SUB_STATE"); do
    sftp_sub_delete "$owner" "$sub"
  done
}

case "$cmd" in

  # ---- systemctl --------------------------------------------------------
  systemctl)
    [[ $# -ge 2 ]] || deny "usage: systemctl <service> <action>"
    service="$1"; action="$2"
    is_allowed_service "$service" || deny "service not allowed: $service"
    is_in "$action" "${ALLOWED_ACTIONS[@]}" || deny "action not allowed: $action"
    if [[ "$action" == "stop" && ( "$service" == "opanel-api" || "$service" == "redis-server" ) ]]; then
      deny "refusing to stop panel-critical service: $service"
    fi
    exec systemctl "$action" "$service"
    ;;

  daemon-reload)
    exec systemctl daemon-reload
    ;;

  # ---- web server (OpenLiteSpeed) --------------------------------------
  ols-test|nginx-test)
    restart_openlitespeed >/dev/null 2>&1 \
      || deny "OpenLiteSpeed configuration test failed"
    echo "OpenLiteSpeed configuration OK"
    ;;

  ols-reload|nginx-reload)
    ols_sync_main_config
    restart_openlitespeed
    ;;
  ols-sync-main)
    ols_sync_main_config
    restart_openlitespeed 2>/dev/null || true
    echo "OpenLiteSpeed main config synced"
    ;;

  panel-ipv6-set)
    [[ $# -eq 1 ]] || deny "usage: panel-ipv6-set <on|off>"
    case "$1" in
      on)  panel_bind="::" ;;
      off) panel_bind="0.0.0.0" ;;
      *) deny "usage: panel-ipv6-set <on|off>" ;;
    esac
    # Write the panel bind before touching the web server: if the OLS sync
    # fails, the admin must still get a panel back on the address family they
    # just asked for.
    env_set PANEL_BIND_HOST "$panel_bind"
    set_outbound_ipv4_preference "$1"
    if [[ "$1" == "on" ]]; then
      iptables_ensure_opanel_chains
      iptables_refresh_standard_ports
      firewall_persist_rules 2>/dev/null || true
    fi
    # The listeners follow the flag the panel already wrote to
    # panel-settings.json.
    ols_sync_main_config || true
    restart_openlitespeed 2>/dev/null || true
    schedule_panel_restart
    echo "IPv6 $1 (panel bind ${panel_bind})"
    ;;

  refresh-tools)
    refresh_tools_ols
    echo "Tools vhost (phpMyAdmin) config refreshed"
    ;;
  ols-custom-write|nginx-custom-write)
    [[ $# -eq 1 ]] || deny "usage: ols-custom-write <domain>"
    domain="$1"
    require_domain "$domain"
    ensure_ols_conf_dir_writable
    target="${OLS_CUSTOM_DIR}/${domain}.conf"
    tmp="${target}.tmp.$$"
    cat >"$tmp"
    if file_has_nul "$tmp"; then
      rm -f "$tmp"
      deny "custom OLS include contains NUL byte"
    fi
    install -m 0664 -o root -g opanel "$tmp" "$target"
    rm -f "$tmp"
    ;;
  ols-custom-delete|nginx-custom-delete)
    [[ $# -eq 1 ]] || deny "usage: ols-custom-delete <domain>"
    domain="$1"
    require_domain "$domain"
    rm -f "${OLS_CUSTOM_DIR}/${domain}.conf"
    ;;

  fastcgi-cache-clear)
    # OLS does not use nginx-style FastCGI caching; no-op for compatibility.
    ;;

  # ---- updates ----------------------------------------------------------
  updates-status)
    echo "opanel release status:"
    if [[ -f "${opanel_DATA_DIR}/update-status.json" ]]; then
      cat "${opanel_DATA_DIR}/update-status.json"
    else
      echo "No update status file found."
    fi
    echo ""
    echo "APT upgradable packages:"
    apt list --upgradable 2>/dev/null | sed -n '1,60p' || true
    echo ""
    echo "Unattended upgrades:"
    systemctl is-enabled unattended-upgrades.service 2>/dev/null || true
    systemctl is-active unattended-upgrades.service 2>/dev/null || true
    echo ""
    echo "Panel auto update timer:"
    systemctl is-enabled opanel-auto-update.timer 2>/dev/null || true
    systemctl list-timers opanel-auto-update.timer apt-daily-upgrade.timer --no-pager 2>/dev/null || true
    echo ""
    echo "OS update service:"
    systemctl is-active opanel-os-update.service 2>/dev/null | sed 's/^inactive$/idle/' || true
    journalctl -u opanel-os-update.service -n 16 --no-pager 2>/dev/null | grep -v "Failed to open /run/systemd/transient" || true
    echo ""
    echo "Panel update service:"
    systemctl is-active opanel-panel-update.service 2>/dev/null | sed 's/^inactive$/idle/' || true
    journalctl -u opanel-panel-update.service -n 16 --no-pager 2>/dev/null | grep -v "Failed to open /run/systemd/transient" || true
    echo ""
    echo "Panel update log:"
    if command -v journalctl >/dev/null 2>&1 && systemctl cat opanel-panel-update.service >/dev/null 2>&1; then
      journalctl -u opanel-panel-update.service -n 60 --no-pager 2>/dev/null | grep -v "Failed to open /run/systemd/transient" || true
    fi
    if [[ ! -s /dev/stdin ]]; then :; fi
    if [[ -f /var/log/opanel-panel-update.log ]]; then
      echo "--- /var/log/opanel-panel-update.log (tail) ---"
      tail -n 60 /var/log/opanel-panel-update.log 2>/dev/null || true
    fi
    ;;

  updates-os-run)
    run_os_update
    ;;

  updates-os-auto)
    [[ $# -eq 3 ]] || deny "usage: updates-os-auto <on|off> <security|all> <on|off>"
    configure_unattended_upgrades "$1" "$2" "$3"
    ;;

  updates-panel-run)
    run_panel_update
    ;;

  updates-panel-auto)
    [[ $# -eq 2 ]] || deny "usage: updates-panel-auto <on|off> <HH:MM>"
    write_panel_auto_update_timer "$1" "$2"
    ;;

  # ---- WAF --------------------------------------------------------------
  waf-status)
    waf_status
    ;;

  waf-install)
    install_waf_engine
    ;;

  ols-vhost-write|ols-vhost-write-defer)
    [[ $# -ge 1 ]] || deny "usage: ols-vhost-write <domain> [hostname ...]"
    safe_domain="$1"
    require_domain "$safe_domain"
    shift
    for hostname in "$@"; do
      require_domain "$hostname"
    done
    vhost_conf="$OLS_VHOSTS_DIR/$safe_domain/vhost.conf"
    vhost_tmp="$(mktemp)"
    cat >"$vhost_tmp"
    # The site's PHP runs as its own Linux user and cannot write the
    # OLS-owned <domain>.error.log, so PHP's error_log points at a per-domain
    # directory the site user owns. Made before the unchanged-vhost shortcut so
    # a refresh always leaves it in place.
    vhost_site_user="$(sed -nE 's#^[[:space:]]*docRoot[[:space:]]+/home/([^/]+)/.*#\1#p' "$vhost_tmp" | head -1)"
    if [[ -n "$vhost_site_user" ]] && id "$vhost_site_user" >/dev/null 2>&1; then
      ensure_php_log_dir "$safe_domain" "$vhost_site_user"
    fi
    # A bulk refresh re-renders every vhost with identical output; when the file
    # is byte-identical and already in place there is nothing to sync or restart.
    if [[ -f "$vhost_conf" ]] && cmp -s "$vhost_tmp" "$vhost_conf"; then
      rm -f "$vhost_tmp"
      echo "vhost unchanged: ${safe_domain}"
      exit 0
    fi
    install -d -o root -g opanel -m 2775 "$OLS_VHOSTS_DIR/$safe_domain"
    install -m 0644 -o root -g opanel "$vhost_tmp" "$vhost_conf"
    rm -f "$vhost_tmp"
    chown -R root:opanel "$OLS_VHOSTS_DIR/$safe_domain"
    chmod 2775 "$OLS_VHOSTS_DIR/$safe_domain"
    chmod 0644 "$vhost_conf"
    # The -defer variant only stages the vhost file; the caller (e.g. a bulk
    # DirectAdmin import writing dozens of vhosts) is responsible for a single
    # `ols-sync-main` afterwards instead of one OLS restart per vhost.
    if [[ "$cmd" == "ols-vhost-write" ]]; then
      ols_sync_main_config
      restart_openlitespeed 2>/dev/null || true
    fi
    ;;

  ols-vhost-delete)
    [[ $# -eq 1 ]] || deny "usage: ols-vhost-delete <domain>"
    safe_domain="$1"
    require_domain "$safe_domain"
    rm -rf "$OLS_VHOSTS_DIR/$safe_domain"
    # The site's WAF rules are written per vhost and referenced by nothing else,
    # so they go with it. Leaving them behind accumulated orphaned rule files --
    # 42 of them on a box that had deleted that many sites.
    rm -f "/usr/local/lsws/conf/opanel/waf/sites/${safe_domain}.conf"
    # Same for its logs: the vhost's access and error log, and the per-domain
    # directory holding its PHP error log. Every caller of this subcommand is
    # removing the site for good, and 54 deleted domains had left 162 of these.
    rm -f "/var/log/openlitespeed/${safe_domain}.access.log" "/var/log/openlitespeed/${safe_domain}.error.log"
    rm -rf "/var/log/openlitespeed/${safe_domain}" "${PHP_LOG_ROOT:?}/${safe_domain}"
    ols_sync_main_config
    restart_openlitespeed 2>/dev/null || true
    ;;

  # The other half of the suspend path that was never implemented. Renaming the
  # file is enough on its own because ols_sync_main_config enumerates vhosts
  # with glob("*/vhost.conf"), so a suspended site drops out of the generated
  # main config and its host mapping instead of leaving a dangling configFile
  # reference that would stop OpenLiteSpeed loading.
  ols-vhost-suspend)
    [[ $# -eq 1 ]] || deny "usage: ols-vhost-suspend <domain>"
    safe_domain="$1"
    require_domain "$safe_domain"
    vhost_conf="$OLS_VHOSTS_DIR/$safe_domain/vhost.conf"
    if [[ -f "$vhost_conf" ]]; then
      mv -f -- "$vhost_conf" "${vhost_conf}.suspended" \
        || deny "could not suspend the vhost for $safe_domain"
      ols_sync_main_config
      restart_openlitespeed 2>/dev/null || true
      echo "suspended $safe_domain"
    else
      # Nothing served is already the state suspend is trying to reach, so
      # this is a no-op rather than an error. Denying here meant one Website
      # row without an OLS vhost -- a failed provision, or a row that outlived
      # its config -- aborted the whole suspension after the Linux account had
      # already been locked, leaving the account half-suspended and, because
      # every retry hit the same domain, impossible to suspend ever again.
      echo "no vhost to suspend for $safe_domain"
    fi
    ;;

  ols-vhost-restore)
    [[ $# -eq 1 ]] || deny "usage: ols-vhost-restore <domain>"
    safe_domain="$1"
    require_domain "$safe_domain"
    vhost_conf="$OLS_VHOSTS_DIR/$safe_domain/vhost.conf"
    if [[ -f "${vhost_conf}.suspended" ]]; then
      mv -f -- "${vhost_conf}.suspended" "$vhost_conf" \
        || deny "could not restore the vhost for $safe_domain"
      ols_sync_main_config
      restart_openlitespeed 2>/dev/null || true
      echo "restored $safe_domain"
    else
      # Symmetric with ols-vhost-suspend: nothing suspended is already the
      # state unsuspend wants, so it must not block the rest of the account
      # coming back.
      echo "no suspended vhost for $safe_domain"
    fi
    ;;

  # openlitespeed.test_config() has always called this and always got "unknown
  # command" back, so with check=False the configuration test silently reported
  # success for every input.
  ols-config-test)
    [[ $# -eq 0 ]] || deny "usage: ols-config-test"
    if [[ -x /usr/local/lsws/bin/litespeed ]]; then
      /usr/local/lsws/bin/litespeed -t 2>&1 || deny "OpenLiteSpeed configuration test failed"
    else
      deny "OpenLiteSpeed binary not found"
    fi
    ;;

  # ---- ClamAV malware scanning (optional) -------------------------------
  addon-status)
    require_addon_id "${1:-}"
    case "$1" in
      fail2ban) addon_fail2ban_status ;;
      mail) addon_mail_status ;;
      dns) addon_dns_status ;;
    esac
    ;;

  addon-install)
    require_addon_id "${1:-}"
    case "$1" in
      fail2ban) addon_fail2ban_install ;;
      mail) addon_mail_install ;;
      dns) addon_dns_install ;;
    esac
    ;;

  addon-uninstall)
    require_addon_id "${1:-}"
    case "$1" in
      fail2ban) addon_fail2ban_uninstall ;;
      mail) addon_mail_uninstall ;;
      dns) addon_dns_uninstall ;;
    esac
    ;;

  addon-enable)
    require_addon_id "${1:-}"
    case "$1" in
      fail2ban)
        systemctl enable fail2ban >/dev/null 2>&1 || true
        systemctl restart fail2ban || deny "fail2ban failed to start"
        addon_fail2ban_wait_for_chains
        iptables_restore_addon_precedence
        echo "fail2ban started"
        ;;
      mail) addon_mail_enable ;;
      dns) addon_dns_enable ;;
    esac
    ;;

  addon-disable)
    require_addon_id "${1:-}"
    case "$1" in
      fail2ban)
        systemctl disable --now fail2ban >/dev/null 2>&1 || true
        echo "fail2ban stopped"
        ;;
      mail) addon_mail_disable ;;
      dns) addon_dns_disable ;;
    esac
    ;;

  addon-fail2ban-configure)
    # Settings arrive as JSON on stdin, so no value is ever interpolated into a
    # command line. Every field is re-validated while rendering the file: the
    # panel validates for the user's benefit, the helper for the box's.
    command -v fail2ban-client >/dev/null 2>&1 || deny "fail2ban is not installed"
    install -d -o opanel -g opanel -m 0750 /var/lib/opanel/addons 2>/dev/null || \
      install -d -m 0750 /var/lib/opanel/addons
    umask 077
    cat >"$FAIL2BAN_SETTINGS_FILE.tmp"
    python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$FAIL2BAN_SETTINGS_FILE.tmp" \
      || { rm -f "$FAIL2BAN_SETTINGS_FILE.tmp"; deny "settings were not valid JSON"; }
    mv "$FAIL2BAN_SETTINGS_FILE.tmp" "$FAIL2BAN_SETTINGS_FILE"
    addon_fail2ban_write_filter
    addon_fail2ban_write_jails
    systemctl restart fail2ban || deny "fail2ban rejected the new settings -- check: journalctl -u fail2ban"
    addon_fail2ban_wait_for_chains
    iptables_restore_addon_precedence
    echo "fail2ban reconfigured"
    ;;

  addon-fail2ban-banned)
    addon_fail2ban_banned
    ;;

  addon-fail2ban-unban)
    require_addon_ip "${1:-}"
    command -v fail2ban-client >/dev/null 2>&1 || deny "fail2ban is not installed"
    # fail2ban-client prints the number it lifted; the panel shows this stdout
    # to the admin as a notice, so let only our sentence through.
    fail2ban-client unban "$1" >/dev/null || deny "could not unban $1"
    echo "$1 unbanned"
    ;;

  addon-fail2ban-log)
    addon_fail2ban_log "${1:-40}"
    ;;

  clamav-install)
    install_clamav_engine
    ;;

  clamav-remove)
    [[ $# -eq 0 ]] || deny "usage: clamav-remove"
    remove_clamav_engine
    ;;

  clamav-status)
    if command -v clamd >/dev/null 2>&1 || command -v clamscan >/dev/null 2>&1; then
      installed=1
    else
      installed=0
    fi
    if systemctl is-active --quiet clamav-daemon 2>/dev/null; then
      running=1
    else
      running=0
    fi
    if lmd_installed; then lmd=1; else lmd=0; fi
    lmd_ver="$( [[ -f /usr/local/maldetect/VERSION ]] && tr -d '[:space:]' </usr/local/maldetect/VERSION || echo '' )"
    if systemctl is-active --quiet opanel-maldet-monitor.service 2>/dev/null; then monitor=1; else monitor=0; fi
    echo "installed=${installed} running=${running} lmd=${lmd} lmd_version=${lmd_ver} monitor=${monitor}"
    ;;

  maldet-ensure)
    # Add LMD to a box that already runs ClamAV (called from opanel-update).
    command -v clamdscan >/dev/null 2>&1 || deny "ClamAV is not installed"
    if lmd_installed; then
      configure_lmd
      echo "Linux Malware Detect already present"
    else
      install_lmd_engine
    fi
    ;;

  maldet-monitor)
    [[ $# -eq 1 ]] || deny "usage: maldet-monitor <enable|disable>"
    case "$1" in
      enable) enable_lmd_monitor ;;
      disable) disable_lmd_monitor ;;
      # ExecStartPre of opanel-maldet-monitor.service.
      prestart) lmd_stop_stray_monitor ;;
      *) deny "usage: maldet-monitor <enable|disable>" ;;
    esac
    ;;

  malware-sigs-update)
    update_malware_signatures
    ;;

  malware-quarantine)
    # <list> | <add PATH [SIG]> | <restore ID> | <drop ID>
    [[ $# -ge 1 && $# -le 3 ]] || deny "usage: malware-quarantine <list|add|restore|drop> [args]"
    quarantine_dispatch "$@"
    ;;

  clamav-start)
    install -d -o clamav -g clamav -m 0755 /run/clamav 2>/dev/null || true
    # Both, as the package ships them: the socket unit is what brings clamd
    # up on the first connection after a reboot.
    systemctl enable clamav-daemon.socket >/dev/null 2>&1 || true
    systemctl enable --now clamav-daemon.service
    echo "clamav-daemon started"
    ;;

  clamav-stop)
    # The socket unit too. Left listening, it starts clamd again on the next
    # connection -- the panel's own status check was enough -- so "stopped"
    # never gave the memory back.
    systemctl disable --now clamav-daemon.socket clamav-daemon.service 2>/dev/null       || systemctl stop clamav-daemon.socket clamav-daemon.service
    echo "clamav-daemon stopped"
    ;;

  clamav-scan-system)
    [[ $# -le 2 ]] || deny "usage: clamav-scan-system [path] [full|incremental]"
    scan_root="${1:-/}"
    scan_mode="${2:-full}"
    [[ "$scan_mode" == "full" || "$scan_mode" == "incremental" ]] || deny "invalid scan mode: $scan_mode"
    # Anchored allowlist: absolute, and no whitespace, quotes or shell
    # metacharacters that could survive into a log line or a filename glob.
    [[ "$scan_root" =~ ^/[A-Za-z0-9._/-]*$ ]] || deny "unsafe scan path"
    case "$scan_root" in
      ".."|"../"*|*"/.."|*"/../"*) deny "path traversal not allowed" ;;
    esac
    scan_root="$(readlink -m "$scan_root")" || deny "cannot resolve $scan_root"
    [[ -d "$scan_root" ]] || deny "scan path is not a directory: $scan_root"
    run_clamav_system_scan "$scan_root" "$scan_mode"
    ;;

  waf-update)
    write_modsec_main_conf
    restart_openlitespeed
    echo "opanel lightweight WAF rules refreshed"
    ;;

  waf-default-rules)
    write_waf_default_rules
    exec cat /usr/local/lsws/conf/opanel/waf/opanel-default.conf
    ;;

  waf-custom-rules)
    touch /usr/local/lsws/conf/opanel/waf/opanel-custom.conf
    exec cat /usr/local/lsws/conf/opanel/waf/opanel-custom.conf
    ;;

  waf-custom-save)
    save_waf_custom_rules
    ;;
  waf-site-rules)
    [[ $# -eq 1 ]] || deny "usage: waf-site-rules <domain>"
    require_domain "$1"
    exec cat "/usr/local/lsws/conf/opanel/waf/sites/${1}.conf"
    ;;
  waf-site-save)
    [[ $# -eq 1 ]] || deny "usage: waf-site-save <domain>"
    save_waf_site_rules "$1"
    ;;
  waf-site-save-defer)
    [[ $# -eq 1 ]] || deny "usage: waf-site-save-defer <domain>"
    save_waf_site_rules "$1" defer
    ;;
  # ---- PHP installation --------------------------------------------------
  php-install)
    [[ $# -eq 1 ]] || deny "usage: php-install <version>"
    install_php_version "$1"
    ;;

  php-ext-status)
    [[ $# -eq 1 ]] || deny "usage: php-ext-status <version>"
    php_ext_status "$1"
    ;;

  php-ext-install)
    [[ $# -eq 2 ]] || deny "usage: php-ext-install <version> <extension>"
    php_ext_install "$1" "$2"
    ;;

  php-ext-remove)
    [[ $# -eq 2 ]] || deny "usage: php-ext-remove <version> <extension>"
    php_ext_remove "$1" "$2"
    ;;

  php-ext-install-all)
    [[ $# -ge 2 ]] || deny "usage: php-ext-install-all <extension> <version>..."
    php_ext_install_all "$@"
    ;;

  php-config-write)
    [[ $# -eq 1 ]] || deny "usage: php-config-write <version>"
    write_php_config "$1"
    ;;

  php-fpm-retune)
    [[ $# -eq 0 ]] || deny "usage: php-fpm-retune"
    retune_php_fpm_pools
    ;;

  mariadb-retune)
    [[ $# -eq 0 ]] || deny "usage: mariadb-retune"
    retune_mariadb
    ;;

  # ---- panel runtime ----------------------------------------------------
  panel-url-set)
    [[ $# -eq 3 ]] || deny "usage: panel-url-set <http|https> <host> <port>"
    scheme="$1"; host="$2"; port="$3"
    require_panel_scheme "$scheme"
    require_panel_host "$host"
    require_port "$port"
    env_set PANEL_PORT "$port"
    env_set PANEL_URL "${scheme}://${host}:${port}"
    env_set ALLOWED_ORIGINS "${scheme}://${host}:${port}"
    if is_domain "$host"; then
      env_set PANEL_DOMAIN "$host"
    else
      env_set PANEL_DOMAIN ""
    fi
    if [[ "$scheme" == "http" ]]; then
      env_set PANEL_SSL_CERT ""
      env_set PANEL_SSL_KEY ""
    fi
    allow_panel_port "$port"
    refresh_tools_ols
    schedule_panel_restart
    echo "Panel URL: ${scheme}://${host}:${port}"
    ;;

  panel-ssl-install)
    [[ $# -ge 2 && $# -le 3 ]] || deny "usage: panel-ssl-install <domain> <port> [email]"
    domain="$1"; port="$2"; email="${3:-}"
    require_domain "$domain"
    require_port "$port"
    env_set PANEL_DOMAIN "$domain"
    env_set PANEL_PORT "$port"
    install -d -o root -g opanel -m 0755 /var/www/opanel-acme/.well-known/acme-challenge
    refresh_tools_ols
    certbot_args=(certonly --webroot -w /var/www/opanel-acme
      --cert-name "$domain" \
      --agree-tos \
      --non-interactive \
      --deploy-hook "install -d -o root -g opanel -m 0750 /etc/opanel && install -m 0640 -o root -g opanel /etc/letsencrypt/live/${domain}/fullchain.pem /etc/opanel/panel-fullchain.pem && install -m 0640 -o root -g opanel /etc/letsencrypt/live/${domain}/privkey.pem /etc/opanel/panel-privkey.pem")
    if [[ -n "$email" ]]; then
      require_email "$email"
      certbot_args+=(--email "$email")
    else
      certbot_args+=(--register-unsafely-without-email)
    fi
    certbot_args+=(--expand -d "$domain")
    certbot "${certbot_args[@]}"
    env_set PANEL_URL "https://${domain}:${port}"
    env_set ALLOWED_ORIGINS "https://${domain}:${port}"
    copy_panel_live_certificate "$domain"
    panel_cert_store_sync
    if [[ -n "$email" ]]; then
      env_set SSL_EMAIL "$email"
    fi
    allow_panel_port "$port"
    refresh_tools_ols
    schedule_panel_restart
    echo "Panel SSL enabled: https://${domain}:${port}"
    ;;

  # ---- Email addon -------------------------------------------------------
  mail-sync)
    # The whole mail state as JSON on stdin (app/services/mail.py).
    [[ $# -eq 0 ]] || deny "usage: mail-sync < state.json"
    require_mail_installed
    mail_sync_from_stdin
    ;;

  mail-configure)
    # Relay, rate limits and spam scores as JSON on stdin; every value is
    # checked again while the configuration is rendered.
    [[ $# -eq 0 ]] || deny "usage: mail-configure < settings.json"
    require_mail_installed
    (
      umask 077
      cat >"${MAIL_SETTINGS_FILE}.tmp"
    )
    python3 -c 'import json,sys; assert isinstance(json.load(open(sys.argv[1])), dict)' "${MAIL_SETTINGS_FILE}.tmp" \
      || { rm -f "${MAIL_SETTINGS_FILE}.tmp"; deny "mail settings were not a JSON object"; }
    chown root:root "${MAIL_SETTINGS_FILE}.tmp"
    chmod 0600 "${MAIL_SETTINGS_FILE}.tmp"
    mv -f "${MAIL_SETTINGS_FILE}.tmp" "$MAIL_SETTINGS_FILE"
    mail_write_relay_credentials
    mail_write_exim_config
    mail_write_rspamd_config
    systemctl reload exim4 >/dev/null 2>&1 || systemctl restart exim4 || deny "exim4 did not take the new settings"
    systemctl reload rspamd >/dev/null 2>&1 || systemctl restart rspamd >/dev/null 2>&1 || true
    echo "mail settings applied"
    ;;

  mail-dkim)
    require_mail_installed
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: mail-dkim <domain> [rotate]"
    [[ -z "${2:-}" || "$2" == "rotate" ]] || deny "usage: mail-dkim <domain> [rotate]"
    mail_dkim_ensure "$1" "${2:-}"
    ;;

  mail-purge-mailbox)
    [[ $# -eq 1 ]] || deny "usage: mail-purge-mailbox <address>"
    mail_purge_mailbox "$1"
    ;;

  mail-purge-domain)
    [[ $# -eq 1 ]] || deny "usage: mail-purge-domain <domain>"
    mail_purge_domain "$1"
    ;;

  mail-usage)
    [[ $# -eq 0 ]] || deny "usage: mail-usage"
    mail_usage
    ;;

  mail-webmail-host)
    require_mail_installed
    [[ $# -ge 2 && $# -le 3 ]] || deny "usage: mail-webmail-host <domain> on|off [email]"
    require_domain "$1"
    case "$2" in
      on) mail_webmail_host_add "$1" "${3:-}" ;;
      off)
        mail_webmail_host_remove "webmail.$1"
        ols_sync_main_config
        restart_openlitespeed
        echo "webmail.$1 removed"
        ;;
      *) deny "usage: mail-webmail-host <domain> on|off [email]" ;;
    esac
    ;;

  mail-log)
    [[ $# -le 1 ]] || deny "usage: mail-log [lines]"
    count="${1:-100}"
    [[ "$count" =~ ^[0-9]{1,4}$ ]] || count=100
    tail -n "$count" /var/log/exim4/mainlog 2>/dev/null || true
    ;;

  mail-rspamd)
    # Read-only views of Rspamd for the panel: its controller's statistics
    # and scan history (loopback, no password needed), and its log.
    require_mail_installed
    case "${1:-}" in
      stat|history)
        [[ $# -eq 1 ]] || deny "usage: mail-rspamd stat|history|log [lines]"
        python3 - "$1" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(f"http://127.0.0.1:11334/{sys.argv[1]}", timeout=15) as res:
        sys.stdout.write(res.read().decode("utf-8", "replace"))
except Exception as exc:
    sys.exit(f"opanel-helper: Rspamd did not answer: {exc}")
PY
        ;;
      log)
        [[ $# -le 2 ]] || deny "usage: mail-rspamd log [lines]"
        count="${2:-300}"
        [[ "$count" =~ ^[0-9]{1,5}$ ]] || count=300
        tail -n "$count" /var/log/rspamd/rspamd.log 2>/dev/null || true
        ;;
      *) deny "usage: mail-rspamd stat|history|log [lines]" ;;
    esac
    ;;

  mail-queue)
    [[ $# -eq 0 ]] || deny "usage: mail-queue"
    exim4 -bpc 2>/dev/null || echo 0
    ;;

  host-trim)
    [[ $# -eq 0 ]] || deny "usage: host-trim"
    host_trim
    ;;

  log-hygiene)
    [[ $# -eq 0 ]] || deny "usage: log-hygiene"
    ensure_site_log_rotation
    ensure_ols_server_log_level
    ensure_ols_js_expires
    ensure_php_jit_disabled
    ensure_journal_cap
    ensure_lmd_monitor_layout
    ensure_ols_defaults_private
    # A subshell: a failed mail refresh must not stop the update's other steps.
    ( mail_refresh ) || echo "WARNING: the Email addon could not be refreshed" >&2
    ( dns_refresh ) || echo "WARNING: DNS Manager could not be refreshed" >&2
    echo "Log hygiene applied"
    ;;

  panel-cert-sync)
    [[ $# -eq 0 ]] || deny "usage: panel-cert-sync"
    panel_cert_store_sync
    echo "Panel certificate store synced"
    ;;

  # Regenerate only the self-signed default. The panel calls this at start-up
  # when no certificate loads, so it must stay cheap: a full store sync copies
  # every Let's Encrypt certificate on the box, which is 180 files on a busy
  # server and not what a panel trying to come up needs to wait for.
  panel-cert-selfsigned)
    [[ $# -eq 0 ]] || deny "usage: panel-cert-selfsigned"
    install -d -o root -g opanel -m 0750 "$PANEL_CERT_STORE"
    rm -f "${PANEL_CERT_STORE}/_default/fullchain.pem" "${PANEL_CERT_STORE}/_default/privkey.pem"
    panel_self_signed_ensure
    ;;

  # ---- certbot ----------------------------------------------------------
  certbot-issue)
    [[ $# -ge 1 ]] || deny "usage: certbot-issue <domain> [alias-domain ...] [email]"
    domain="$1"; shift
    email=""
    domains=("$domain")
    require_domain "$domain"
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == *@* ]]; then
        [[ $# -eq 1 ]] || deny "email must be the final certbot-issue argument"
        email="$1"
        shift
        break
      fi
      require_domain "$1"
      domains+=("$1")
      shift
    done
    install -d -o root -g opanel -m 0755 /var/www/opanel-acme/.well-known/acme-challenge
    # Ensure OLS serves ACME challenges via the webroot
    refresh_tools_ols
    args=(certonly --webroot -w /var/www/opanel-acme --cert-name "$domain" --non-interactive --agree-tos --expand)
    for cert_domain in "${domains[@]}"; do
      args+=(-d "$cert_domain")
    done
    if [[ -n "$email" ]]; then
      require_email "$email"
      args+=(--email "$email")
    else
      args+=(--register-unsafely-without-email)
    fi
    certbot "${args[@]}"
    restart_openlitespeed
    copy_panel_live_certificate "$domain"
    panel_cert_store_sync
    echo "SSL certificate issued for ${domain}"
    ;;

  certbot-renew)
    certbot renew --quiet
    panel_cert_store_sync
    schedule_panel_restart
    ;;
  certbot-renew-soon)
    [[ $# -le 1 ]] || deny "usage: certbot-renew-soon [days]"
    renew_ssl_soon "${1:-10}"
    ;;
  certbot-auto-renew-install)
    write_ssl_auto_renew_timer
    echo "SSL auto-renew timer installed"
    ;;
  certbot-dns-cloudflare)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: certbot-dns-cloudflare <domain> [email]   (token on stdin)"
    issue_cloudflare_wildcard "$1" "${2:-}"
    ;;
  certbot-dns-local)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: certbot-dns-local <domain> [email]"
    issue_local_dns_wildcard "$1" "${2:-}"
    ;;
  certbot-dns-cloudflare-remove)
    [[ $# -eq 1 ]] || deny "usage: certbot-dns-cloudflare-remove <domain>"
    remove_cloudflare_wildcard "$1"
    ;;
  manual-ssl-install)
    [[ $# -eq 1 ]] || deny "usage: manual-ssl-install <domain>"
    install_manual_ssl "$1"
    ;;
  manual-ssl-remove)
    [[ $# -eq 1 ]] || deny "usage: manual-ssl-remove <domain>"
    remove_manual_ssl "$1"
    ;;

  # ---- firewall (iptables) ---------------------------------------------
  iptables-status)
    echo "Chains: OPANEL_INPUT, OPANEL_USER, OPANEL_BLOCKLIST"
    echo ""
    echo "=== OPANEL_INPUT ==="
    iptables -L OPANEL_INPUT -n --line-numbers 2>/dev/null || echo "  (chain not found)"
    echo ""
    echo "=== OPANEL_USER ==="
    iptables -L OPANEL_USER -n --line-numbers 2>/dev/null || echo "  (chain not found)"
    echo ""
    echo "=== OPANEL_BLOCKLIST ==="
    iptables -L OPANEL_BLOCKLIST -n --line-numbers 2>/dev/null || echo "  (chain not found)"
    echo ""
    echo "=== ipsets ==="
    total="$(ipset list "$BLOCKLIST_IPSET_V4" 2>/dev/null | grep -Ec '^[0-9]' || true)"
    echo "  ${BLOCKLIST_IPSET_V4}: ${total:-0} network(s)"
    total="$(ipset list "$BLOCKLIST_IPSET_V6" 2>/dev/null | grep -Ec '^[0-9a-fA-F:]+/' || true)"
    echo "  ${BLOCKLIST_IPSET_V6}: ${total:-0} network(s)"
    ;;
  iptables-check-enabled)
    if iptables -C INPUT -j OPANEL_BLOCKLIST 2>/dev/null && \
       iptables -C INPUT -j OPANEL_INPUT 2>/dev/null && \
       iptables -C INPUT -j OPANEL_USER 2>/dev/null; then
      echo yes
    else
      echo no
    fi
    ;;
  iptables-rules-store-ensure)
    ensure_firewall_rule_store
    echo "opanel firewall rule store ready"
    ;;
  iptables-enable)
    iptables -P INPUT ACCEPT 2>/dev/null || true
    ip6tables -P INPUT ACCEPT 2>/dev/null || true
    iptables_flush_managed_chains
    iptables_reorder_managed_jumps
    iptables_add_default_allowances
    firewall_blocklist_apply 2>/dev/null || true
    # The reorder above just re-inserted the panel's jumps at 1/2/3, pushing any
    # addon chain below the blanket port allowances and silently disabling every
    # ban it holds. Put them back on top of OPANEL_INPUT.
    iptables_restore_addon_precedence 2>/dev/null || true
    # Without this the chains live only in memory. A box that reboots comes
    # back with whatever netfilter-persistent last saved -- which on one live
    # server meant OPANEL_INPUT and OPANEL_USER at zero references and an
    # INPUT policy of ACCEPT, so nothing was being filtered at all.
    firewall_persist_rules
    echo "opanel iptables chains enabled"
    ;;
  iptables-persist)
    [[ $# -eq 0 ]] || deny "usage: iptables-persist"
    firewall_persist_rules
    echo "opanel firewall rules persisted"
    ;;
  iptables-disable)
    # Remove chain references from INPUT (rules inside chains are preserved)
    iptables -D INPUT -j OPANEL_BLOCKLIST 2>/dev/null || true
    iptables -D INPUT -j OPANEL_INPUT 2>/dev/null || true
    iptables -D INPUT -j OPANEL_USER 2>/dev/null || true
    ip6tables -D INPUT -j OPANEL_BLOCKLIST 2>/dev/null || true
    ip6tables -D INPUT -j OPANEL_INPUT 2>/dev/null || true
    ip6tables -D INPUT -j OPANEL_USER 2>/dev/null || true
    iptables -P INPUT ACCEPT 2>/dev/null || true
    ip6tables -P INPUT ACCEPT 2>/dev/null || true
    firewall_persist_rules
    echo "opanel iptables chains disconnected from INPUT"
    ;;
  iptables-reload)
    firewall_blocklist_apply 2>/dev/null || true
    firewall_persist_rules
    echo "opanel iptables rules reloaded"
    ;;
  iptables-run)
    run_managed_iptables_command iptables "$@"
    ;;
  ip6tables-run)
    run_managed_iptables_command ip6tables "$@"
    ;;
  ipset-run)
    run_managed_ipset_command "$@"
    ;;
  iptables-allow-port)
    [[ $# -eq 2 ]] || deny "usage: iptables-allow-port <port> <proto>"
    require_port "$1"; require_proto "$2"
    iptables -A OPANEL_USER -p "$2" --dport "$1" -j ACCEPT -m comment --comment "opanel:UserZone" 2>/dev/null \
      || iptables -A OPANEL_USER -p "$2" --dport "$1" -j ACCEPT 2>/dev/null \
      || true
    ;;
  iptables-panel-allow-port)
    [[ $# -eq 1 ]] || deny "usage: iptables-panel-allow-port <port>"
    allow_panel_port "$1"
    ;;
  iptables-allow-ip)
    [[ $# -ge 1 && $# -le 3 ]] || deny "usage: iptables-allow-ip <ip> [port] [proto]"
    run_ip_rule allow "$1" "${2:-}" "${3:-tcp}"
    ;;
  iptables-deny-ip)
    [[ $# -ge 1 && $# -le 3 ]] || deny "usage: iptables-deny-ip <ip> [port] [proto]"
    run_ip_rule deny "$1" "${2:-}" "${3:-tcp}"
    ;;
  iptables-delete)
    [[ $# -eq 1 && "$1" =~ ^[0-9]+$ ]] || deny "usage: iptables-delete <rule-number>"
    iptables -D OPANEL_USER "$1" 2>/dev/null \
      || iptables -D OPANEL_INPUT "$1" 2>/dev/null \
      || deny "could not delete rule $1"
    echo "Rule $1 deleted"
    ;;

  # ---- firewall blocklist -----------------------------------------------
  firewall-blocklist-status|iptables-blocklist-status|blocklist-status|nginx-blocklist-status)
    firewall_blocklist_status
    ;;
  firewall-blocklist-timer-install|iptables-blocklist-timer-install|blocklist-timer-install|nginx-blocklist-timer-install)
    firewall_blocklist_write_timer
    echo "Blocklist timer installed"
    ;;
  firewall-blocklist-add|iptables-blocklist-add|blocklist-add|nginx-blocklist-add)
    [[ $# -eq 1 ]] || deny "usage: firewall-blocklist-add <url>"
    firewall_blocklist_add_url "$1"
    ;;
  firewall-blocklist-delete|iptables-blocklist-delete|blocklist-delete|nginx-blocklist-delete)
    [[ $# -eq 1 ]] || deny "usage: firewall-blocklist-delete <url>"
    firewall_blocklist_delete_url "$1"
    ;;
  firewall-blocklist-run|iptables-blocklist-run|blocklist-run|nginx-blocklist-run)
    [[ $# -eq 0 ]] || deny "usage: firewall-blocklist-run"
    firewall_blocklist_run
    ;;

  # ---- filesystem -------------------------------------------------------
  chown-www)
    deny "chown-www has been removed: site files belong to the site's own Linux user, use fix-permissions"
    ;;

  fix-permissions)
    [[ $# -ge 1 && $# -le 2 ]] || deny "usage: fix-permissions <path> [site-user]"
    target=$(require_managed_path "$1" "${2:-}")
    site_user="${2:-}"
    if [[ -z "$site_user" ]]; then
      # Every managed site lives at /home/<site-user>/<domain>, so the owning
      # user is the first path segment. Deriving it beats the old behaviour of
      # falling back to www-data, which handed one shared account ownership of
      # the tree and broke isolation between sites.
      site_user="${target#${HOME_ROOT}/}"
      site_user="${site_user%%/*}"
    fi
    require_linux_user "$site_user"
    fix_site_tree "$target" "$site_user"
    ;;

  site-path-fix)
    [[ $# -eq 2 ]] || deny "usage: site-path-fix <path> <site-user>"
    target=$(require_managed_path "$1" "$2")
    fix_site_tree "$target" "$2"
    ;;

  site-document-root-ensure)
    [[ $# -eq 3 ]] || deny "usage: site-document-root-ensure <site-user> <site-root> <relative-path>"
    user="$1"; root_arg="$2"; rel_arg="$3"
    ensure_sites_group
    require_linux_user "$user"
    root_target=$(require_managed_path "$root_arg" "$user")
    [[ "$rel_arg" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ ]] || deny "unsafe relative path: $rel_arg"
    case "$rel_arg" in
      ""|"/"|/*|*$'\n'*|"."|".."|"./"*|"../"*|*"/."|*"/.."|*"/./"*|*"/../"*) deny "unsafe relative path: $rel_arg" ;;
    esac
    target=$(require_safe_path "$root_target" "$root_target/$rel_arg")
    mkdir -p -- "$target"
    harden_site_dir_path "$root_target" "$target" "$user"
    ;;

  site-file-write)
    [[ $# -eq 3 || $# -eq 4 ]] || deny "usage: site-file-write <site-user> <site-root> <relative-path> [0644|0640]"
    user="$1"; root_arg="$2"; rel_arg="$3"; mode_arg="${4:-0644}"
    require_linux_user "$user"
    [[ "$mode_arg" == "0644" || "$mode_arg" == "0640" ]] || deny "invalid file mode: $mode_arg"
    root_target=$(require_managed_path "$root_arg" "$user")
    case "$rel_arg" in
      ""|"/"|/*|*$'\n'*|".."|"../"*|*"/.."|*"/../"*) deny "unsafe relative path: $rel_arg" ;;
    esac
    # Test the raw join, not the resolved path: require_safe_path returns the
    # output of `readlink -m`, so its last component is already dereferenced and
    # -L on it can never be true.
    [[ ! -L "$root_target/$rel_arg" ]] || deny "refusing to write through a symlink: $rel_arg"
    target=$(require_safe_path "$root_target" "$root_target/$rel_arg")
    [[ -d "$target" ]] && deny "cannot write a directory: $target"
    parent=$(dirname -- "$target")
    runuser -u "$user" -- mkdir -p -- "$parent"
    harden_site_dir_path "$root_target" "$parent" "$user"
    existing_mode=""
    if [[ -e "$target" ]]; then
      existing_mode=$(stat -c '%a' -- "$target")
    fi
    base=$(basename -- "$target")
    tmp="$parent/.${base}.opanel-write-$$"
    rm -f -- "$tmp"
    cat >"$tmp"
    chown "$user:$user" "$tmp"
    chmod "$mode_arg" "$tmp"
    mv -f -- "$tmp" "$target"
    ;;

  site-file-install)
    [[ $# -eq 4 ]] || deny "usage: site-file-install <site-user> <site-root> <relative-path> <staged-path>"
    user="$1"; root_arg="$2"; rel_arg="$3"; staged_arg="$4"
    require_linux_user "$user"
    root_target=$(require_managed_path "$root_arg" "$user")
    case "$rel_arg" in
      ""|"/"|/*|*$'\n'*|".."|"../"*|*"/.."|*"/../"*) deny "unsafe relative path: $rel_arg" ;;
    esac
    # Raw join, for the reason given in site-file-write above.
    [[ ! -L "$root_target/$rel_arg" ]] || deny "refusing to write through a symlink: $rel_arg"
    target=$(require_safe_path "$root_target" "$root_target/$rel_arg")
    [[ "$staged_arg" == /tmp/opanel-upload-* ]] || deny "invalid staged upload path"
    [[ ! -L "$staged_arg" ]] || deny "staged upload cannot be a symlink"
    staged=$(readlink -e -- "$staged_arg") || deny "staged upload not found"
    [[ "$staged" == /tmp/opanel-upload-* && -f "$staged" ]] || deny "invalid staged upload"
    [[ "$(stat -c '%U' -- "$staged")" == "opanel" ]] || deny "staged upload must be owned by opanel"
    parent=$(dirname -- "$target")
    runuser -u "$user" -- mkdir -p -- "$parent"
    harden_site_dir_path "$root_target" "$parent" "$user"
    base=$(basename -- "$target")
    tmp="$parent/.${base}.opanel-install-$$"
    rm -f -- "$tmp"
    install -o "$user" -g "$user" -m 0644 -- "$staged" "$tmp"
    mv -f -- "$tmp" "$target"
    rm -f -- "$staged"
    ;;

  site-file-delete)
    [[ $# -eq 3 ]] || deny "usage: site-file-delete <site-user> <site-root> <relative-path>"
    user="$1"; root_arg="$2"; rel_arg="$3"
    require_linux_user "$user"
    root_target=$(require_managed_path "$root_arg" "$user")
    case "$rel_arg" in
      ""|"/"|/*|".."|"../"*|*"/.."|*"/../"*) deny "unsafe relative path: $rel_arg" ;;
    esac
    # Raw join, for the reason given in site-file-write above.
    [[ ! -L "$root_target/$rel_arg" ]] || deny "refusing to delete through a symlink: $rel_arg"
    target=$(require_safe_path "$root_target" "$root_target/$rel_arg")
    rm -f -- "$target"
    ;;

  site-backup-restore)
    [[ $# -eq 5 ]] || deny "usage: site-backup-restore <site-user> <site-root> <backup-path> <max-items> <max-bytes>"
    user="$1"; root_arg="$2"; archive_arg="$3"; max_items="$4"; max_bytes="$5"
    require_linux_user "$user"
    [[ "$max_items" =~ ^[0-9]+$ && "$max_bytes" =~ ^[0-9]+$ ]] || deny "invalid restore limits"
    root_target=$(require_managed_path "$root_arg" "$user")
    backup_root="$(env_get BACKUP_ROOT)"
    [[ -n "$backup_root" ]] || backup_root="/var/backups/opanel"
    backup_root=$(readlink -m "$backup_root") || deny "cannot resolve backup root"
    archive_target=$(require_safe_path "$backup_root" "$archive_arg")
    [[ -f "$archive_target" && ! -L "$archive_target" ]] || deny "backup archive not found"
    python3 - "$archive_target" "$root_target" "$max_items" "$max_bytes" <<'PY'
import os
import shutil
import sys
import tarfile

archive_path, destination, max_items, max_bytes = sys.argv[1:]
max_items = int(max_items)
max_bytes = int(max_bytes)
destination = os.path.realpath(destination)


def normalized_name(name, has_site_prefix):
    if "\x00" in name:
        raise ValueError("backup contains an unsafe path")
    name = name.replace("\\", "/")
    if name.startswith("database/"):
        return None
    if has_site_prefix:
        if name == "site" or not name.startswith("site/"):
            return None
        name = name[len("site/"):]
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts) or name.startswith("/"):
        raise ValueError("backup contains an unsafe path")
    if ":" in parts[0]:
        raise ValueError("backup contains an absolute path")
    return os.path.join(*parts)


with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    has_site_prefix = any(member.name == "site" or member.name.startswith("site/") for member in members)
    selected = []
    total = 0
    for member in members:
        name = normalized_name(member.name, has_site_prefix)
        if name is None:
            continue
        if member.issym() or member.islnk() or member.isdev() or not (member.isdir() or member.isfile()):
            raise ValueError("backup links and special files are not allowed")
        target = os.path.abspath(os.path.join(destination, name))
        resolved = os.path.realpath(target)
        if os.path.commonpath((destination, resolved)) != destination:
            raise ValueError("backup path escapes website root")
        selected.append((member, target))
        total += member.size if member.isfile() else 0
        if max_items and len(selected) > max_items:
            raise ValueError("backup has too many files")
        if max_bytes and total > max_bytes:
            raise ValueError("backup is too large")

    for member, target in selected:
        if member.isdir():
            if os.path.islink(target):
                raise ValueError("backup destination contains an unsafe symlink")
            if os.path.lexists(target) and not os.path.isdir(target):
                os.unlink(target)
            os.makedirs(target, exist_ok=True)
            continue
        parent = os.path.dirname(target)
        os.makedirs(parent, exist_ok=True)
        if os.path.islink(target):
            raise ValueError("backup destination contains an unsafe symlink")
        if os.path.isdir(target):
            shutil.rmtree(target)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("backup entry cannot be read")
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with source, os.fdopen(descriptor, "wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
PY
    fix_site_tree "$root_target" "$user"
    ;;

  site-import-copy)
    # Copies an already-extracted directory tree (built by an unprivileged
    # opanel-api staging step, e.g. DirectAdmin backup import) into a site's
    # document root. The site directory is created chown'd to the site's own
    # Linux user (see site-document-root-ensure), so opanel-api itself has no
    # write access to it — this subcommand is the privileged trampoline that
    # places the files, mirroring how site-backup-restore places a raw
    # archive's contents.
    [[ $# -eq 4 ]] || deny "usage: site-import-copy <site-user> <site-root> <relative-path> <staging-source-dir>"
    user="$1"; root_arg="$2"; rel_arg="$3"; source_arg="$4"
    require_linux_user "$user"
    root_target=$(require_managed_path "$root_arg" "$user")
    case "$rel_arg" in
      ""|"/"|/*|*$'\n'*|"."|".."|"./"*|"../"*|*"/."|*"/.."|*"/./"*|*"/../"*) deny "unsafe relative path: $rel_arg" ;;
    esac
    target=$(require_safe_path "$root_target" "$root_target/$rel_arg")
    mkdir -p -- "$target"
    source_target=$(readlink -m "$source_arg") || deny "cannot resolve staging source"
    case "$source_target" in
      /tmp/opanel-da-import-*) ;;
      *) deny "staging source outside expected da-import temp dir: $source_target" ;;
    esac
    [[ -d "$source_target" && ! -L "$source_target" ]] || deny "staging source not found"
    python3 - "$source_target" "$target" <<'PY'
import os
import shutil
import sys

source, destination = sys.argv[1:3]
source = os.path.realpath(source)
destination = os.path.realpath(destination)


def _contained(path):
    return os.path.commonpath((destination, path)) == destination


for root, dirs, files in os.walk(source):
    dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    rel = os.path.relpath(root, source)
    target_dir = destination if rel == "." else os.path.abspath(os.path.join(destination, rel))
    if not _contained(target_dir):
        raise ValueError("import path escapes website root")
    if os.path.islink(target_dir):
        raise ValueError("import destination contains an unsafe symlink")
    os.makedirs(target_dir, exist_ok=True)
    for name in files:
        src = os.path.join(root, name)
        if os.path.islink(src):
            continue
        dst = os.path.abspath(os.path.join(target_dir, name))
        if not _contained(dst):
            raise ValueError("import path escapes website root")
        if os.path.islink(dst):
            raise ValueError("import destination contains an unsafe symlink")
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        descriptor = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with open(src, "rb") as handle, os.fdopen(descriptor, "wb") as output:
            shutil.copyfileobj(handle, output, length=1024 * 1024)
PY
    fix_site_tree "$root_target" "$user"
    ;;

  site-archive-extract)
    [[ $# -eq 7 ]] || deny "usage: site-archive-extract <site-user> <site-root> <archive-path> <destination-path> <zip|tar.gz> <max-items> <max-bytes>"
    user="$1"; root_arg="$2"; archive_rel="$3"; destination_rel="$4"; archive_kind="$5"
    max_items="$6"; max_bytes="$7"
    require_linux_user "$user"
    [[ "$archive_kind" == "zip" || "$archive_kind" == "tar.gz" ]] || deny "unsupported archive type"
    [[ "$max_items" =~ ^[0-9]+$ && "$max_bytes" =~ ^[0-9]+$ ]] || deny "invalid archive limits"
    root_target=$(require_managed_path "$root_arg" "$user")
    archive_target=$(require_safe_path "$root_target" "$root_target/$archive_rel")
    destination_target=$(require_safe_path "$root_target" "$root_target/$destination_rel")
    [[ -f "$archive_target" && ! -L "$archive_target" ]] || deny "archive not found"
    [[ -d "$destination_target" && ! -L "$destination_target" ]] || deny "archive destination not found"
    tmp_archive=$(mktemp "/tmp/opanel-extract-XXXXXX")
    trap 'rm -f -- "$tmp_archive"' EXIT
    install -o "$user" -g "$user" -m 0600 -- "$archive_target" "$tmp_archive"
    runuser -u "$user" -- python3 - "$tmp_archive" "$archive_kind" "$destination_target" "$max_items" "$max_bytes" "$archive_target" <<'PY'
import os
import shutil
import stat
import sys
import tarfile
import zipfile

archive_path, archive_kind, destination = sys.argv[1:4]
max_items, max_bytes = int(sys.argv[4]), int(sys.argv[5])
source_archive = os.path.realpath(sys.argv[6])
destination = os.path.realpath(destination)

# Symlinks, hardlinks and device nodes are never recreated from an archive --
# they are the classic write-through-a-link escalation vector. Rather than
# rejecting the whole archive when it contains one (a stock WordPress export
# ships e.g. Query Monitor's wp-content/db.php symlink), skip those entries and
# extract everything else. Count them so the extraction can say what it dropped.
skipped_specials = 0


def safe_target(name):
    """Normalize backslash paths and resolve to a safe absolute path."""
    if "\x00" in name:
        raise ValueError("archive contains an unsafe path")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ValueError("archive contains an absolute path")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("archive contains an unsafe path")
    target = os.path.abspath(os.path.join(destination, *parts))
    resolved = os.path.realpath(target)
    if os.path.commonpath((destination, resolved)) != destination:
        raise ValueError("archive path escapes destination")
    return target, resolved


def zip_implied_dirs(infos):
    implied = set()
    for info in infos:
        parts = [part for part in info.filename.replace("\\", "/").split("/") if part not in ("", ".")]
        for index in range(1, len(parts)):
            implied.add("/".join(parts[:index]))
    return implied


def _is_dir_entry(info, implied_dirs):
    """Return True if a ZipInfo represents a directory."""
    normalized = info.filename.replace("\\", "/")
    if info.is_dir() or normalized.endswith("/"):
        return True
    mode = (info.external_attr >> 16) & 0o170000
    if stat.S_ISDIR(mode) and info.file_size == 0:
        return True
    if info.file_size == 0 and normalized.rstrip("/") in implied_dirs:
        return True
    return False


def is_source_archive(resolved):
    return resolved == source_archive


def ensure_regular_target(target):
    if os.path.islink(target):
        raise ValueError("refusing to overwrite a symlink")
    if os.path.isdir(target):
        raise ValueError("archive file conflicts with an existing directory")


def ensure_directory_target(target):
    if os.path.islink(target):
        raise ValueError("refusing to overwrite a symlink")
    if os.path.exists(target) and not os.path.isdir(target):
        try:
            if os.path.getsize(target) == 0:
                return
        except OSError:
            pass
        raise ValueError("archive directory conflicts with an existing file")


def validate_zip():
    global skipped_specials
    count = 0
    total = 0
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        implied_dirs = zip_implied_dirs(infos)
        for info in infos:
            count += 1
            if max_items and count > max_items:
                raise ValueError("archive has too many files")
            target, resolved = safe_target(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                skipped_specials += 1
                continue
            if is_source_archive(resolved):
                continue
            if _is_dir_entry(info, implied_dirs):
                ensure_directory_target(target)
                continue
            ensure_regular_target(target)
            total += info.file_size
            if max_bytes and total > max_bytes:
                raise ValueError("archive is too large")


def extract_zip():
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        implied_dirs = zip_implied_dirs(infos)
        for info in infos:
            if stat.S_ISLNK((info.external_attr >> 16) & 0o170000):
                continue
            target, resolved = safe_target(info.filename)
            if is_source_archive(resolved):
                continue
            if _is_dir_entry(info, implied_dirs):
                if os.path.exists(target) and not os.path.isdir(target):
                    os.unlink(target)
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                with archive.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
            except RuntimeError as exc:
                raise ValueError("archive entry cannot be extracted") from exc


def validate_tar():
    global skipped_specials
    count = 0
    total = 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            count += 1
            if max_items and count > max_items:
                raise ValueError("archive has too many files")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                skipped_specials += 1
                continue
            target, resolved = safe_target(member.name)
            if is_source_archive(resolved):
                continue
            if not member.isdir() and not member.isfile():
                skipped_specials += 1
                continue
            if member.isdir():
                ensure_directory_target(target)
                continue
            ensure_regular_target(target)
            total += member.size
            if max_bytes and total > max_bytes:
                raise ValueError("archive is too large")


def extract_tar():
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                continue
            target, resolved = safe_target(member.name)
            if is_source_archive(resolved):
                continue
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            if not member.isfile():
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with source, open(target, "wb") as dst:
                shutil.copyfileobj(source, dst, length=1024 * 1024)


if archive_kind == "zip":
    validate_zip()
    extract_zip()
else:
    validate_tar()
    extract_tar()

if skipped_specials:
    sys.stderr.write(
        "opanel: skipped %d symlink/special entr%s (not extracted for safety)\n"
        % (skipped_specials, "y" if skipped_specials == 1 else "ies")
    )
PY
    # The archive may contain an entry with its own filename. Restore the
    # original source archive after extraction so it cannot overwrite itself.
    install -o "$user" -g "$user" -m 0644 -- "$tmp_archive" "$archive_target"
    fix_site_tree "$destination_target" "$user"
    rm -f -- "$tmp_archive"
    trap - EXIT
    ;;

  panel-user-ensure)
    [[ $# -eq 1 ]] || deny "usage: panel-user-ensure <panel-user>"
    ensure_panel_user_home "$1"
    ;;

  panel-user-password)
    [[ $# -eq 1 ]] || deny "usage: panel-user-password <panel-user>"
    set_panel_user_password "$1"
    ;;

  panel-user-delete)
    [[ $# -eq 1 ]] || deny "usage: panel-user-delete <panel-user>"
    delete_panel_user_runtime "$1"
    ;;

  sftp-sub-create)
    [[ $# -eq 3 ]] || deny "usage: sftp-sub-create <owner> <name> <folder>  (password on stdin)"
    sftp_sub_create "$1" "$2" "$3"
    ;;

  sftp-sub-password)
    [[ $# -eq 2 ]] || deny "usage: sftp-sub-password <owner> <name>  (password on stdin)"
    sftp_sub_password "$1" "$2"
    ;;

  sftp-sub-delete)
    [[ $# -eq 2 ]] || deny "usage: sftp-sub-delete <owner> <name>"
    sftp_sub_delete "$1" "$2"
    ;;

  # provisioning.suspend_account and unsuspend_account have called these two
  # since they were written, but the case labels never existed: the calls fell
  # through to the default arm's "unknown command", and because both call sites
  # pass check=False the non-zero exit was discarded and the provisioning job
  # was still recorded "completed". A suspended account therefore kept SFTP,
  # its websites, its crontab and its MariaDB grants. See
  # docs/whmcs-opanel-contract.md, Suspend step 2 / Unsuspend step 1.
  panel-user-lock)
    [[ $# -eq 1 ]] || deny "usage: panel-user-lock <panel-user>"
    require_linux_user "$1"
    id -u "$1" >/dev/null 2>&1 || deny "panel Linux user does not exist: $1"
    usermod -L "$1" || deny "could not lock $1"
    # Locking only stops the next authentication. Existing SFTP sessions keep
    # their file access until the process dies, so they are cut here.
    pkill -KILL -u "$1" 2>/dev/null || true
    echo "locked $1"
    ;;

  panel-user-unlock)
    [[ $# -eq 1 ]] || deny "usage: panel-user-unlock <panel-user>"
    require_linux_user "$1"
    id -u "$1" >/dev/null 2>&1 || deny "panel Linux user does not exist: $1"
    usermod -U "$1" || deny "could not unlock $1"
    echo "unlocked $1"
    ;;

  site-runtime-ensure)
    [[ $# -eq 3 ]] || deny "usage: site-runtime-ensure <site-user> <path> <php-version|none>"
    user="$1"; path="$2"; php_version="$3"
    require_linux_user "$user"
    target=$(require_managed_path "$path" "$user")
    ensure_panel_user_home "$user"
    if [[ -d "$target/public" && ! -e "$target/public_html" ]]; then
      mv "$target/public" "$target/public_html"
    elif [[ -d "$target/public" && -d "$target/public_html" && -z "$(find "$target/public_html" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      rmdir "$target/public_html"
      mv "$target/public" "$target/public_html"
    fi
    mkdir -p "$target/public_html"
    harden_site_dir_path "$target" "$target/public_html" "$user"
    fix_site_tree "$target" "$user"
    ensure_php_pool "$user" "$target" "$php_version"
    ;;

  site-runtime-move)
    [[ $# -eq 4 ]] || deny "usage: site-runtime-move <site-user> <old-path> <new-path> <php-version|none>"
    user="$1"; old_path="$2"; new_path="$3"; php_version="$4"
    require_linux_user "$user"
    old_target=$(require_managed_path "$old_path")
    new_target=$(require_managed_path "$new_path" "$user")
    old_user="${old_target#${HOME_ROOT}/}"
    old_user="${old_user%%/*}"
    ensure_panel_user_home "$user"
    if [[ "$old_target" != "$new_target" ]]; then
      [[ ! -e "$new_target" ]] || deny "target path already exists: $new_target"
      delete_site_php_pools "$old_user" "$old_target"
      mkdir -p "$(dirname "$new_target")"
      mv "$old_target" "$new_target"
    fi
    if [[ -d "$new_target/public" && ! -e "$new_target/public_html" ]]; then
      mv "$new_target/public" "$new_target/public_html"
    fi
    mkdir -p "$new_target/public_html"
    harden_site_dir_path "$new_target" "$new_target/public_html" "$user"
    fix_site_tree "$new_target" "$user"
    ensure_php_pool "$user" "$new_target" "$php_version"
    ;;

  site-runtime-delete)
    [[ $# -eq 2 ]] || deny "usage: site-runtime-delete <site-user> <path>"
    user="$1"; path="$2"
    require_linux_user "$user"
    target=$(require_managed_path "$path" "$user")
    delete_site_php_pools "$user" "$target"
    exec rm -rf "$target"
    ;;

  rm-site)
    [[ $# -eq 3 ]] || deny "usage: rm-site <site-user> <site-root> <path>"
    user="$1"; root="$2"; path="$3"
    target=$(require_bound_managed_path "$user" "$root" "$path")
    delete_no_follow "$user" "$root" "$target"
    ;;

  mkdir-site)
    [[ $# -eq 1 ]] || deny "usage: mkdir-site <path>"
    target=$(require_managed_path "$1")
    install -d -o www-data -g www-data -m 0750 "$target"
    install -d -o www-data -g www-data -m 0750 "$target/public_html"
    ;;

  site-log-read)
    [[ $# -eq 3 ]] || deny "usage: site-log-read <domain> <access|error> <lines>"
    read_site_log "$1" "$2" "$3"
    ;;

  waf-access-log-read)
    [[ $# -ge 2 ]] || deny "usage: waf-access-log-read <lines> <domain>..."
    read_waf_access_logs "$@"
    ;;

  waf-access-log-clear)
    [[ $# -ge 1 ]] || deny "usage: waf-access-log-clear <domain>..."
    clear_waf_access_logs "$@"
    ;;

  # ---- WP-CLI as www-data ----------------------------------------------
  # pcre.jit=0 + opcache.jit=disable: the ionCube loader sets a user opcode
  # handler, which the opcache JIT refuses ("JIT is incompatible with third
  # party extensions..."). Turning JIT off up front keeps wp-cli output clean.
  wp)
    # Removed. This ran wp-cli as www-data with no validation beyond an
    # argument count, and wp-cli bootstraps WordPress from --path, so pointing
    # it at a tenant's document root executed that tenant's wp-config.php and
    # plugins as www-data -- an account that ensure_panel_user_home adds to
    # every panel user's private group and ensure_sites_group adds to
    # opanel-sites, which owns phpMyAdmin's config-db.php and
    # blowfish_secret.inc.php. The backend reached it whenever
    # Website.linux_user was NULL, which is the permanent state of every row
    # created before that column was added. Callers now resolve the site user
    # with site_users.require_site_linux_user and use wp-site.
    # Backward-compatible shim, and nothing more. An existing box always runs
    # the PREVIOUS update.sh when it upgrades, and that script validates the
    # freshly-installed helper with `opanel-helper wp --info`. Removing the case
    # outright made that check fail under `set -e`, which aborted the update
    # after the new helper was in place but before migrations ran. Accepting
    # exactly `--info` keeps the upgrade path working; every other argv -- which
    # is what the defect was, since wp-cli bootstraps WordPress from --path --
    # is still refused.
    if [[ $# -eq 1 && "$1" == "--info" ]]; then
      [[ -x /usr/local/bin/wp ]] || deny "wp-cli not found"
      exec runuser -u www-data -- env HOME=/var/www         WP_CLI_PHP_ARGS='-d pcre.jit=0 -d opcache.jit=disable'         php -d pcre.jit=0 -d opcache.jit=disable /usr/local/bin/wp --info
    fi
    deny "wp is no longer supported; use wp-site <site-user> <args...>"
    ;;

  # Fixed-argv health check for the installer's helper validation. `wp --info`
  # touches no site tree and takes nothing from the caller, which is the whole
  # difference from the removed `wp` case: that one forwarded caller-chosen
  # argv, and wp-cli bootstraps WordPress from --path.
  wp-info)
    [[ $# -eq 0 ]] || deny "usage: wp-info"
    [[ -x /usr/local/bin/wp ]] || deny "wp-cli not found"
    exec runuser -u www-data -- env HOME=/var/www       WP_CLI_PHP_ARGS='-d pcre.jit=0 -d opcache.jit=disable'       php -d pcre.jit=0 -d opcache.jit=disable /usr/local/bin/wp --info
    ;;

  wp-site)
    [[ $# -ge 2 ]] || deny "usage: wp-site <site-user> <args...>"
    user="$1"; shift
    require_linux_user "$user"
    exec runuser -u "$user" -- env HOME="$HOME_ROOT/$user" WP_CLI_PHP_ARGS='-d pcre.jit=0 -d opcache.jit=disable' php -d pcre.jit=0 -d opcache.jit=disable /usr/local/bin/wp "$@"
    ;;

  # ---- crontab managed for www-data ------------------------------------
  # The user argument is mandatory and always validated. It used to default to
  # www-data and skip require_linux_user for that value, so every site whose
  # linux_user was NULL shared one crontab: cron-list handed the whole thing to
  # the caller and cron-write replaced it wholesale, letting one tenant read
  # and overwrite another's scheduled commands.
  cron-list)
    [[ $# -eq 1 ]] || deny "usage: cron-list <site-user>"
    require_linux_user "$1"
    exec runuser -u "$1" -- crontab -l 2>/dev/null
    ;;
  cron-write)
    # crontab content is fed via stdin
    [[ $# -eq 1 ]] || deny "usage: cron-write <site-user>"
    require_linux_user "$1"
    exec runuser -u "$1" -- crontab -
    ;;

  # ---- service status (read-only, no privilege change needed but useful)
  service-status)
    [[ $# -eq 1 ]] || deny "usage: service-status <service>"
    is_allowed_service "$1" || deny "service not allowed: $1"
    exec systemctl status "$1" --no-pager
    ;;

  # ---- terminal command execution as panel Linux user ------------------
  terminal-exec)
    # Execute a whitelisted command as the panel Linux user
    # Args: <site-user> <cwd> [--php-version=<version>] <command> [args...]
    [[ $# -ge 3 ]] || deny "usage: terminal-exec <site-user> <cwd> [--php-version=<version>] <command> [args...]"
    user="$1"; cwd_arg="$2"; shift 2
    php_version=""
    if [[ "${1:-}" == --php-version=* ]]; then
      php_version="${1#--php-version=}"
      require_php_version "$php_version"
      shift
    fi
    [[ $# -ge 1 ]] || deny "usage: terminal-exec <site-user> <cwd> [--php-version=<version>] <command> [args...]"
    cmd="$1"; shift
    require_linux_user "$user"
    id -u "$user" >/dev/null 2>&1 || deny "panel Linux user does not exist: $user"
    target=$(require_terminal_cwd "$cwd_arg" "$user")

    install -d -o "$user" -g "$user" -m 0700 "$HOME_ROOT/$user/.composer" "$HOME_ROOT/$user/.npm"
    # Validate cwd exists immediately before cd to avoid TOCTOU
    [[ -d "$target" ]] || deny "working directory does not exist: $target"
    cd "$target" || deny "failed to change to working directory: $target"
    umask 027
    terminal_env=(
      "HOME=$HOME_ROOT/$user"
      "COMPOSER_HOME=$HOME_ROOT/$user/.composer"
      "npm_config_cache=$HOME_ROOT/$user/.npm"
      "PATH=/usr/local/bin:/usr/bin:/bin"
    )
    php_bin="php"
    if [[ -n "$php_version" ]]; then
      lsphp_php_bin="/usr/local/lsws/lsphp${php_version//./}/bin/php"
      if [[ -x "$lsphp_php_bin" ]]; then
        php_bin="$lsphp_php_bin"
      else
        deny "PHP CLI is not installed: lsphp${php_version//./}"
      fi
    fi

    # Allowed commands for terminal access.
    #
    # This list is a usability affordance, not the security boundary. Ten of its
    # entries are general-purpose interpreters (php, composer, phpunit, node,
    # npm, npx, yarn, git, artisan, wp) whose first argument is arbitrary code,
    # so no argument policy can narrow what they do. The boundary is the uid
    # they run as -- the site's own Linux user, which the tenant already reaches
    # through their own site PHP -- together with the filesystem modes that
    # keep that uid out of other tenants' trees: /home is 0711, each
    # /home/<user> is 0750 (root-owned, group the site user), and the panel
    # account is a group member so the file manager and backups still work.
    #
    # The file-command branches below additionally confine every path argument
    # to the caller's own home and refuse the options that run another program.
    case "$cmd" in
      php)
        exec runuser -u "$user" -- env "${terminal_env[@]}" "$php_bin" "$@"
        ;;
      composer)
        composer_bin="$(command -v composer || true)"
        [[ -n "$composer_bin" ]] || deny "composer not found"
        exec runuser -u "$user" -- env "${terminal_env[@]}" "$php_bin" "$composer_bin" "$@"
        ;;
      phpunit)
        phpunit_bin="$(command -v phpunit || true)"
        [[ -n "$phpunit_bin" ]] || deny "phpunit not found"
        exec runuser -u "$user" -- env "${terminal_env[@]}" "$php_bin" "$phpunit_bin" "$@"
        ;;
      node|npm|npx|yarn|git)
        exec runuser -u "$user" -- env "${terminal_env[@]}" "$cmd" "$@"
        ;;
      ls|cat|mkdir|rm|cp|mv|chmod|chown|grep|find|tar|zip|unzip|diff|head|tail|less|du|df)
        require_terminal_path_args "$user" "$target" "$@"
        exec runuser -u "$user" -- env "${terminal_env[@]}" "$cmd" "$@"
        ;;
      pwd|echo|touch|date|whoami|which|clear)
        if [[ "$cmd" == "touch" ]]; then
          require_terminal_path_args "$user" "$target" "$@"
        fi
        exec runuser -u "$user" -- env "${terminal_env[@]}" "$cmd" "$@"
        ;;
      curl|wget)
        require_terminal_download_args "$user" "$target" "$@"
        exec runuser -u "$user" -- env "${terminal_env[@]}" "$cmd" "$@"
        ;;
      artisan)
        # artisan is a PHP script, executed via php
        [[ -f artisan ]] || deny "artisan not found in $target"
        exec runuser -u "$user" -- env "${terminal_env[@]}" "$php_bin" artisan "$@"
        ;;
      wp)
        # WP-CLI as the site user with the selected PHP version. JIT off (pcre
        # + opcache) -- the ionCube loader's user opcode handler is incompatible
        # with the opcache JIT and otherwise prints a warning on every command.
        [[ -x /usr/local/bin/wp ]] || deny "wp-cli not found"
        exec runuser -u "$user" -- env "${terminal_env[@]}" WP_CLI_PHP_ARGS='-d pcre.jit=0 -d opcache.jit=disable' "$php_bin" -d pcre.jit=0 -d opcache.jit=disable /usr/local/bin/wp "$@"
        ;;
      *)
        echo "Command not allowed: $cmd" >&2
        echo "Allowed commands: php, composer, artisan, wp, node, npm, npx, yarn, git, phpunit, ls, cat, mkdir, rm, cp, mv, chmod, chown, pwd, echo, touch, grep, find, tar, zip, unzip, curl, wget, diff, head, tail, less, du, df, date, whoami, which, clear" >&2
        exit 126
        ;;
    esac
    ;;

  *)
    deny "unknown command: $cmd"
    ;;
esac
