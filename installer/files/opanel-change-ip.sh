#!/usr/bin/env bash
# Move OPanel from an old server IP to a new one (`opanel change-ip`).
#
# OPanel had no such script: `opanel change-ip` pointed at
# /usr/local/sbin/opanel-change-ip, which nothing installed, and an IP changed
# by hand left the panel hostname on Settings at the old address - Settings
# reads panel_url from /var/lib/opanel/panel-settings.json before the .env
# (operator, 2026-11-09). BPanel's change_IP.sh, for OPanel's files:
#
#   - the panel's .env (PANEL_URL, ALLOWED_ORIGINS...), its own state files in
#     /var/lib/opanel (panel-settings.json - the hostname on Settings - and the
#     addons' settings, the DNS Manager's server address among them),
#     /root/login.txt and /etc/hosts;
#   - LiteSpeed's configuration (httpd_config.conf, the panel's vhosts);
#   - the DNS Manager's zones: A records and SPF ip4: of the old address, each
#     changed zone's serial raised.
#
# Every file it changes is copied under /root/opanel-ip-change.XXXXXX first.
# Text files only; backups (*.bak*, RCS ,v) and keys/certificates are left alone.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/opanel}"
DATA_DIR="${opanel_DATA_DIR:-/var/lib/opanel}"
DNS_DB="${DNS_DB:-/var/lib/opanel-dns/pdns.sqlite3}"
LOGIN_FILE="${LOGIN_FILE:-/root/login.txt}"

usage() {
  cat <<'EOF'
Usage:
  opanel change-ip                      (asks for both)
  opanel-change-ip <old-ip> <new-ip>
EOF
}
die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }

is_ipv4() {
  local value="$1" part
  local -a parts
  [[ "$value" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS=. read -r -a parts <<<"$value"
  for part in "${parts[@]}"; do
    (( 10#$part <= 255 )) || return 1
  done
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
[[ $EUID -eq 0 ]] || die "Run as root"

old_ip="${1:-}"
new_ip="${2:-}"
if [[ -z "$old_ip" ]]; then
  read -rp "Old IP: " old_ip
fi
if [[ -z "$new_ip" ]]; then
  current_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  read -rp "New IP${current_ip:+ [$current_ip]}: " new_ip
  new_ip="${new_ip:-$current_ip}"
fi
is_ipv4 "$old_ip" || die "Invalid old IP: $old_ip"
is_ipv4 "$new_ip" || die "Invalid new IP: $new_ip"
# Run at every boot of an image (`opanel change-ip <image-ip> $(hostname -I ...)`),
# the image's own server sees its own address: nothing to move, not an error.
if [[ "$old_ip" == "$new_ip" ]]; then
  echo "The server is still on $old_ip; nothing to change."
  exit 0
fi

backup_root="$(mktemp -d /root/opanel-ip-change.XXXXXX)"
old_ip_ere="${old_ip//./\\.}"
declare -A seen=()
declare -a files=() changed_files=()

add_candidate() {
  local path="$1" resolved
  [[ -e "$path" ]] || return 0
  resolved="$(readlink -f -- "$path" 2>/dev/null || true)"
  [[ -n "$resolved" && -f "$resolved" ]] || return 0
  case "$resolved" in
    *.bak|*.bak.*|*.opanel.bak|*,v|*.pem|*.key|*.crt|*.csr|*.db|*.sqlite*) return 0 ;;
  esac
  if [[ -z "${seen[$resolved]+x}" ]]; then
    seen["$resolved"]=1
    files+=("$resolved")
  fi
}

collect_tree() {
  local root="$1" path
  [[ -d "$root" ]] || return 0
  while IFS= read -r -d '' path; do
    add_candidate "$path"
  done < <(find "$root" -xdev \
      \( -path '*/.git/*' -o -path '*/.venv/*' -o -path '*/node_modules/*' -o -path '*/__pycache__/*' -o -path '*/assets/*' -o -path '*/dist/*' \) -prune -o \
      -type f \( -name '.env' -o -name '*.conf' -o -name '*.json' -o -name '*.txt' -o -name '*.ini' -o -name '*.service' -o -name '*.php' \) \
      -print0 2>/dev/null)
}

shopt -s nullglob
for path in "$APP_DIR/backend/.env" "$LOGIN_FILE" /etc/hosts /usr/local/lsws/conf/httpd_config.conf; do
  add_candidate "$path"
done
shopt -u nullglob
collect_tree "$DATA_DIR"
collect_tree /usr/local/lsws/conf/opanel
collect_tree /etc/opanel
collect_tree /etc/systemd/system

for file in "${files[@]}"; do
  if grep -IqE "(^|[^0-9.])${old_ip_ere}([^0-9.]|$)" "$file"; then
    dest="$backup_root$file"
    mkdir -p "$(dirname "$dest")"
    cp -a -- "$file" "$dest"
    perl -0pi -e "s/(?<![0-9.])\\Q$old_ip\\E(?![0-9.])/$new_ip/g" "$file"
    changed_files+=("$file")
  fi
done

# Settings shows panel_url from panel-settings.json first: make it the .env's
# when the two now differ (one was set to the old address, the other was not).
env_url="$(awk -F= '$1 == "PANEL_URL" { sub(/^[^=]*=/, ""); print; exit }' "$APP_DIR/backend/.env" 2>/dev/null || true)"
settings_file="$DATA_DIR/panel-settings.json"
if [[ -n "$env_url" && -f "$settings_file" ]]; then
  if python3 - "$settings_file" "$env_url" "$old_ip" <<'PY'
import json, sys
path, env_url, old_ip = sys.argv[1:4]
data = json.load(open(path, encoding="utf-8"))
current = data.get("panel_url") if isinstance(data, dict) else None
# Only a stored URL that still names the old address, or names none, is
# replaced; a hostname the operator chose on Settings stays.
sys.exit(0 if isinstance(data, dict) and current and old_ip in current and current != env_url else 1)
PY
  then
    [[ -e "$backup_root$settings_file" ]] || { mkdir -p "$(dirname "$backup_root$settings_file")"; cp -a -- "$settings_file" "$backup_root$settings_file"; }
    python3 - "$settings_file" "$env_url" <<'PY'
import json, sys
path, env_url = sys.argv[1:3]
data = json.load(open(path, encoding="utf-8"))
data["panel_url"] = env_url
open(path, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
PY
    changed_files+=("$settings_file (panel_url)")
  fi
fi

# The DNS Manager's zones: A records of the old address and SPF ip4: of it.
dns_zones=""
if [[ -f "$DNS_DB" ]]; then
  cp -a -- "$DNS_DB" "$backup_root/pdns.sqlite3"
  dns_zones="$(python3 - "$DNS_DB" "$old_ip" "$new_ip" <<'PY'
import re, sqlite3, sys
path, old, new = sys.argv[1:4]
db = sqlite3.connect(path)
changed = set()
for rid, domain_id, rtype, content in db.execute("SELECT id, domain_id, type, content FROM records WHERE type IN ('A', 'TXT', 'SPF')").fetchall():
    if rtype == "A":
        updated = new if content == old else content
    else:
        updated = re.sub(r"(?<![0-9.])" + re.escape(old) + r"(?![0-9.])", new, content)
    if updated != content:
        db.execute("UPDATE records SET content = ? WHERE id = ?", (updated, rid))
        changed.add(domain_id)
names = [row[0] for row in db.execute(
    "SELECT name FROM domains WHERE id IN (%s)" % ",".join("?" * len(changed)), tuple(changed))] if changed else []
db.commit()
print(" ".join(sorted(names)))
PY
)"
  if [[ -n "$dns_zones" ]]; then
    for zone in $dns_zones; do
      pdnsutil increase-serial "$zone" >/dev/null 2>&1 || warn "Could not raise the serial of $zone"
    done
    pdns_control purge >/dev/null 2>&1 || true
    changed_files+=("DNS zones: $dns_zones")
  fi
fi

if (( ${#changed_files[@]} > 0 )); then
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl restart opanel-api >/dev/null 2>&1 || warn "Could not restart opanel-api"
  if [[ -x /usr/local/lsws/bin/lswsctrl ]]; then
    /usr/local/lsws/bin/lswsctrl restart >/dev/null 2>&1 || warn "Could not restart LiteSpeed"
  fi
  echo "Changed ${#changed_files[@]} item(s) from $old_ip to $new_ip:"
  printf '  %s\n' "${changed_files[@]}"
  echo "Backup: $backup_root"
  echo "Panel URL: ${env_url:-unchanged}"
else
  # Only the DNS database's safety copy can be in it: nothing changed.
  rm -rf -- "$backup_root"
  echo "Nothing named $old_ip; nothing changed."
fi
