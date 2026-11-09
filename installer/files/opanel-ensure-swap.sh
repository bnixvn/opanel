#!/usr/bin/env bash
# Swap for a server that has none, before the panel builds its frontend.
#
# The Vite build needs about 720 MB (measured on .41, 2026-11-09) on top of
# MariaDB, LiteSpeed and PHP. A customer's install on a small VPS with no swap
# stopped at the Vite step: the build was starved or killed and never moved on.
# BPanel's bpanel-memory-guard has done this since its own incident; this is
# its swap half, with the same checks.
#
# Safe to run repeatedly; never fails the install or the update - every step
# that could go wrong on an unusual host is skipped with a reason.
#
# Opt out: touch /etc/opanel/no-swap
set -uo pipefail
SWAPFILE="/swapfile"
SWAP_OPT_OUT="/etc/opanel/no-swap"

say() { echo "  $*"; }
ram_mb() { awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo; }

if [[ $EUID -ne 0 ]]; then
  echo "opanel-ensure-swap must run as root" >&2
  exit 0
fi
if [[ -e "$SWAP_OPT_OUT" ]]; then
  say "swap: disabled by $SWAP_OPT_OUT"
  exit 0
fi
# Any swap at all - a partition, zram, another file - is the operator's policy.
if [[ -n "$(swapon --show --noheadings 2>/dev/null)" ]]; then
  say "swap: already configured, leaving it alone"
  exit 0
fi
# Containers cannot swapon. systemd-detect-virt prints "none" and exits 1 when
# there is none, so its output is read and its exit code ignored.
virt="$(systemd-detect-virt --container 2>/dev/null)"
if [[ -n "$virt" && "$virt" != "none" ]]; then
  say "swap: skipped, this is a $virt container and cannot manage its own swap"
  exit 0
fi
fstype="$(findmnt -no FSTYPE / 2>/dev/null || echo unknown)"
case "$fstype" in
  ext2|ext3|ext4|xfs) ;;
  *)
    say "swap: skipped, root filesystem is $fstype and needs a swapfile created by hand"
    exit 0
    ;;
esac
if [[ -e "$SWAPFILE" ]]; then
  say "swap: $SWAPFILE already exists but is not active, leaving it alone"
  exit 0
fi

ram="$(ram_mb)"
if (( ram <= 2048 )); then
  size="$ram"
else
  size=$(( ram / 2 ))
  (( size < 2048 )) && size=2048
  (( size > 8192 )) && size=8192
fi
free_mb="$(df -Pm / | awk 'NR==2 {print $4}')"
# Leave real room behind: filling the disk to make swap trades one outage for a worse one.
if (( free_mb < size + 10240 )); then
  say "swap: skipped, only ${free_mb}MB free on / and ${size}MB of swap would leave too little"
  exit 0
fi

say "swap: creating ${size}MB at $SWAPFILE"
if ! fallocate -l "${size}M" "$SWAPFILE" 2>/dev/null; then
  dd if=/dev/zero of="$SWAPFILE" bs=1M count="$size" status=none 2>/dev/null || {
    say "swap: could not allocate the file, skipping"
    rm -f "$SWAPFILE"
    exit 0
  }
fi
chmod 0600 "$SWAPFILE"
if ! mkswap "$SWAPFILE" >/dev/null 2>&1; then
  say "swap: mkswap failed, skipping"
  rm -f "$SWAPFILE"
  exit 0
fi
if ! swapon "$SWAPFILE" 2>/dev/null; then
  say "swap: swapon was refused by this kernel, skipping"
  rm -f "$SWAPFILE"
  exit 0
fi
if ! grep -qE "^[^#]*[[:space:]]swap[[:space:]]" /etc/fstab 2>/dev/null; then
  printf '%s none swap sw 0 0\n' "$SWAPFILE" >>/etc/fstab
fi
# Swap as the emergency it is meant to be, not where busy PHP gets paged out.
install -d -m 0755 /etc/sysctl.d
printf 'vm.swappiness=10\n' >/etc/sysctl.d/60-opanel-swap.conf
sysctl -q vm.swappiness=10 2>/dev/null || true
say "swap: active, ${size}MB, vm.swappiness=10"
exit 0
