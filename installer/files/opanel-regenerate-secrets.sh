#!/usr/bin/env bash
# Give a server cloned from an image secrets of its own (`opanel
# regenerate-secrets`; security, 2026-11-10).
#
# Every VPS made from one image starts with the image's secrets, so each one
# accepts the others' sessions and tokens and shares their database password.
# This replaces, on this server only:
#
#   - the panel's SECRET_KEY (sessions, and the passwords it stores encrypted:
#     those are encrypted again under the new key), phpMyAdmin's sign-on and
#     cookie secrets, and the API and MCP tokens (deleted) -- app.regenerate_secrets;
#   - the password of MariaDB's `opanel` user and /opt/opanel/.my.cnf;
#   - the panel's self-signed certificate;
#   - the DNS Manager's API key and the Email addon's webmail secrets, when
#     those addons are installed;
#   - the shared blocklist's reporter key: the server registers as itself.
#
# Everyone signed in to the panel is signed out. The admin's password is not
# changed here; `opanel sync-admin-root-password` or `opanel
# change-admin-password` does that.
#
#   opanel regenerate-secrets           replace them now
#   opanel regenerate-secrets --once    only on a server other than the one it
#                                       last ran on (for an image's boot script)
#
# What it changes is copied under /root/opanel-secrets.XXXXXX first.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/opanel}"
DATA_DIR="${opanel_DATA_DIR:-/var/lib/opanel}"
ENV_FILE="$APP_DIR/backend/.env"
MYCNF="$APP_DIR/.my.cnf"
MARKER="$DATA_DIR/secrets-host"
HELPER=/usr/local/sbin/opanel-helper
DNS_DIR=/etc/opanel-dns
DNS_CONF=/etc/powerdns/pdns.conf
MAIL_DIR=/etc/opanel-mail
WEBMAIL_ENV=/etc/bnix-webmail.env
MAIL_SSO_MASTER=opanel-sso

die() { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }

# This server, as far as an image's clones differ: the machine id, the SSH
# host key (both remade per instance by cloud-init) and the address.
host_identity() {
  {
    cat /etc/machine-id 2>/dev/null || true
    cat /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null | awk '{print $2}' || true
    hostname -I 2>/dev/null | awk '{print $1}' || true
  } | sha256sum | cut -d' ' -f1
}

once=0
case "${1:-}" in
  "") ;;
  --once) once=1 ;;
  -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) die "usage: opanel regenerate-secrets [--once]" ;;
esac
[[ $EUID -eq 0 ]] || die "Run as root"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found"

identity="$(host_identity)"
if [[ $once -eq 1 && -f "$MARKER" && "$(cat "$MARKER" 2>/dev/null)" == "$identity" ]]; then
  echo "This server's secrets were already made here; nothing to change."
  exit 0
fi

backup_root="$(mktemp -d /root/opanel-secrets.XXXXXX)"
chmod 0700 "$backup_root"
keep() {
  local path
  for path in "$@"; do
    [[ -e "$path" ]] || continue
    mkdir -p "$backup_root$(dirname "$path")"
    cp -a -- "$path" "$backup_root$path"
  done
}

db_path="$(awk -F= '$1 == "DATABASE_URL" { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE")"
db_path="${db_path#sqlite:///}"
[[ "$db_path" == /* ]] || db_path="$APP_DIR/backend/${db_path#./}"

systemctl stop opanel-backup-scheduler.timer >/dev/null 2>&1 || true
systemctl stop opanel-api >/dev/null 2>&1 || true
restart_panel() {
  systemctl start opanel-api >/dev/null 2>&1 || warn "opanel-api did not start: journalctl -u opanel-api"
  systemctl start opanel-backup-scheduler.timer >/dev/null 2>&1 || true
}
trap restart_panel EXIT

keep "$ENV_FILE" "$db_path" "$MYCNF" "$DNS_DIR/api.key" "$DNS_CONF" "$DATA_DIR/addons/dns-api.key" \
  "$WEBMAIL_ENV" "$DATA_DIR/addons/mail-sso.secret" "$MAIL_DIR/master-users" \
  /etc/opanel/certs/_default "$MARKER"
while IFS= read -r -d '' json; do
  keep "$json"
done < <(find "$DATA_DIR" -xdev -type f -name '*.json' -print0 2>/dev/null)

# 1. The panel's own: SECRET_KEY and what it encrypts, phpMyAdmin's secrets,
#    the API and MCP tokens.
( cd "$APP_DIR/backend" && sudo -u opanel env HOME="$APP_DIR" opanel_DATA_DIR="$DATA_DIR" \
    "$APP_DIR/backend/.venv/bin/python" -W ignore -m app.regenerate_secrets ) \
  || die "the panel's secrets could not be replaced; nothing of them changed (backup: $backup_root)"
changed=("panel SECRET_KEY, stored passwords re-encrypted, API/MCP tokens deleted" "phpMyAdmin sign-on and cookie secrets")
rm -rf /var/lib/php/pma-sessions/sess_* 2>/dev/null || true
if [[ -x "$HELPER" ]]; then
  sudo -u opanel env HOME="$APP_DIR" sudo -n "$HELPER" refresh-tools >/dev/null 2>&1 \
    || warn "phpMyAdmin's sign-on was not rewritten: run opanel-helper refresh-tools"
fi

# 2. MariaDB's `opanel` user.
if [[ -f "$MYCNF" ]] && command -v mariadb >/dev/null 2>&1; then
  password="$(openssl rand -base64 32 | tr -d '/+=\n' | cut -c1-32)"
  if mariadb <<SQL
ALTER USER 'opanel'@'localhost' IDENTIFIED BY '${password}';
FLUSH PRIVILEGES;
SQL
  then
    python3 - "$MYCNF" "$password" <<'PY'
import re, sys
path, password = sys.argv[1:3]
text = open(path, encoding="utf-8").read()
text = re.sub(r'(?m)^(\s*password\s*=\s*).*$', lambda m: m.group(1) + '"' + password + '"', text)
open(path, "w", encoding="utf-8").write(text)
PY
    changed+=("MariaDB password of the opanel user")
  else
    warn "MariaDB's opanel user kept its password"
  fi
fi

# 3. The self-signed certificate the panel falls back on.
if [[ -x "$HELPER" ]]; then
  sudo -u opanel env HOME="$APP_DIR" sudo -n "$HELPER" panel-cert-selfsigned >/dev/null 2>&1 \
    && changed+=("the panel's self-signed certificate") \
    || warn "the self-signed certificate was not remade"
fi
# ssl-cert's snakeoil, the HTTPS listener's certificate for names no site has.
if command -v make-ssl-cert >/dev/null 2>&1 && [[ -f /etc/ssl/certs/ssl-cert-snakeoil.pem ]]; then
  keep /etc/ssl/certs/ssl-cert-snakeoil.pem /etc/ssl/private/ssl-cert-snakeoil.key
  make-ssl-cert generate-default-snakeoil --force-overwrite >/dev/null 2>&1 \
    && changed+=("the HTTPS listener's snakeoil certificate") \
    || warn "the snakeoil certificate was not remade"
fi

# 4. DNS Manager: its API key (PowerDNS's api-key and the panel's copy).
if [[ -f "$DNS_DIR/installed" && -f "$DNS_CONF" ]]; then
  key="$(openssl rand -hex 32)"
  install -m 0600 -o root -g root /dev/null "$DNS_DIR/api.key.new"
  printf '%s\n' "$key" >"$DNS_DIR/api.key.new"
  mv -f "$DNS_DIR/api.key.new" "$DNS_DIR/api.key"
  install -m 0600 -o opanel -g opanel /dev/null "$DATA_DIR/addons/dns-api.key"
  printf '%s\n' "$key" >"$DATA_DIR/addons/dns-api.key"
  sed -i -E "s/^api-key=.*/api-key=${key}/" "$DNS_CONF"
  systemctl restart pdns >/dev/null 2>&1 || warn "pdns did not restart: journalctl -u pdns"
  changed+=("DNS Manager API key")
fi

# 5. Email addon: the webmail's session and sign-on secrets and the password
#    of its sign-on master user in Dovecot.
if [[ -f "$MAIL_DIR/installed" && -f "$WEBMAIL_ENV" ]]; then
  auth_secret="$(openssl rand -hex 32)"
  sso_secret="$(openssl rand -hex 32)"
  master_password="$(openssl rand -hex 24)"
  sed -i -E -e "s/^AUTH_SECRET=.*/AUTH_SECRET=${auth_secret}/" -e "s/^SSO_SECRET=.*/SSO_SECRET=${sso_secret}/" \
    -e "s/^SSO_MASTER_PASSWORD=.*/SSO_MASTER_PASSWORD=${master_password}/" "$WEBMAIL_ENV"
  install -m 0600 -o opanel -g opanel /dev/null "$DATA_DIR/addons/mail-sso.secret"
  printf '%s\n' "$sso_secret" >"$DATA_DIR/addons/mail-sso.secret"
  hash="$(printf '%s' "$master_password" | openssl passwd -6 -stdin)"
  install -m 0640 -o root -g dovecot /dev/null "$MAIL_DIR/master-users.new"
  printf '%s:{SHA512-CRYPT}%s::::::allow_nets=127.0.0.1/32,::1/128\n' "$MAIL_SSO_MASTER" "$hash" >"$MAIL_DIR/master-users.new"
  mv -f "$MAIL_DIR/master-users.new" "$MAIL_DIR/master-users"
  systemctl reload dovecot >/dev/null 2>&1 || systemctl restart dovecot >/dev/null 2>&1 || warn "dovecot did not reload"
  systemctl restart bnix-webmail >/dev/null 2>&1 || warn "the webmail did not restart"
  changed+=("webmail secrets and its Dovecot sign-on password")
fi

# 6. Shared blocklist: forget the image's reporter key; the next run registers
#    this server as a reporter of its own (approved again on the collector).
state="$DATA_DIR/shared-blocklist.json"
if [[ -f "$state" ]] && grep -q '"key"' "$state"; then
  python3 - "$state" <<'PY'
import json, os, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
if isinstance(data, dict) and data.get("key"):
    data.update(key="", reporter_id=None, status="", last_error="")
    tmp = path + ".regen-tmp"
    open(tmp, "w", encoding="utf-8").write(json.dumps(data, indent=2) + "\n")
    st = os.stat(path)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o777)
    os.replace(tmp, path)
PY
  changed+=("shared blocklist reporter key (registers again)")
fi

# LiteSpeed reads its certificates and phpMyAdmin's settings at start.
if [[ -x /usr/local/lsws/bin/lswsctrl ]]; then
  /usr/local/lsws/bin/lswsctrl restart >/dev/null 2>&1 || warn "Could not restart LiteSpeed"
fi

install -m 0644 -o root -g root /dev/null "$MARKER"
printf '%s\n' "$identity" >"$MARKER"

echo "Replaced on this server:"
printf '  %s\n' "${changed[@]}"
echo "Everyone signed in to the panel has to sign in again."
echo "Backup: $backup_root"
