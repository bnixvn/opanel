#!/usr/bin/env bash
# Emergency recovery for opanel hosts that became unreachable after importing
# very large URL blocklists into UFW.

set -euo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "Run this script as root." >&2; exit 1; }

ts="$(date +%Y%m%d-%H%M%S)"
backup_dir="/root/opanel-ufw-rescue-${ts}"
env_file="/opt/opanel/backend/.env"

mkdir -p "$backup_dir"
ufw status numbered >"${backup_dir}/ufw-status-numbered.txt" 2>&1 || true
iptables-save >"${backup_dir}/iptables-save.txt" 2>&1 || true
ip6tables-save >"${backup_dir}/ip6tables-save.txt" 2>&1 || true
tar -C /etc -czf "${backup_dir}/ufw-etc-backup.tar.gz" ufw 2>/dev/null || true

echo "Backup saved in ${backup_dir}"
echo "Disabling and resetting UFW..."
ufw --force disable || true
ufw --force reset || true

ssh_port="$(sshd -T 2>/dev/null | awk '/^port / {print $2; exit}' || true)"
ssh_port="${ssh_port:-22}"
panel_port=""
if [[ -f "$env_file" ]]; then
  panel_port="$(awk -F= '$1 == "PANEL_PORT" {print $2; exit}' "$env_file" | tr -d '"' || true)"
fi
panel_port="${panel_port:-2222}"

echo "Recreating protected allow rules..."
ufw default deny incoming || true
ufw default allow outgoing || true
ufw allow OpenSSH comment "opanel:PanelZone" || ufw allow OpenSSH || true
ufw allow "${ssh_port}/tcp" comment "opanel:PanelZone" || ufw allow "${ssh_port}/tcp" || true
for port in "$panel_port" 80 443 465 587; do
  [[ "$port" =~ ^[0-9]{1,5}$ ]] || continue
  ufw allow "${port}/tcp" comment "opanel:PanelZone" || ufw allow "${port}/tcp" || true
done

echo "Enabling UFW with clean rules..."
ufw --force enable || true
ufw status numbered || true

echo ""
echo "UFW rescue complete. Next, update opanel so URL blocklists move to Nginx:"
echo "  cd /opt/opanel-source && sudo bash installer/update.sh --release"
