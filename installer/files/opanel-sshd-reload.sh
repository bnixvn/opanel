#!/usr/bin/env bash
# Makes every running sshd use /etc/ssh/sshd_config as it is now.
#
# `systemctl reload ssh` signals only the sshd that systemd tracks. A fresh
# Ubuntu 24.04 VPS showed there can be two: cloud-init's first-boot upgrade
# (openssh-server, then systemd and its daemon-reexec) left the sshd it had
# just started running while systemd forgot it, and the installer's
# `systemctl enable --now ssh` started a second one on the same
# socket-activated port. Each answered about half the logins, and the
# forgotten one kept the configuration it had started with: every reload
# reached the other one only, so half the SFTP logins never met OPanel's
# Match block and got "This account is currently not available" (lab box,
# 2026-11-23). So every sshd listener is told to reload, not only the unit's.
#
# Run by the installer and the updater, and by opanel-helper (through
# systemd-run, outside the API's sandbox) whenever it changes sshd_config.
# Exit 0: every running sshd reads the new configuration, or none runs (a
# socket-activated start reads it). Exit 1: sshd refuses the configuration,
# or a reload failed; the reason goes to stderr.
set -u
PATH=/usr/sbin:/usr/bin:/sbin:/bin

if ! sshd -t; then
  echo "sshd refuses /etc/ssh/sshd_config" >&2
  exit 1
fi

failed=0
main=""
for unit in ssh.service sshd.service; do
  systemctl is-active --quiet "$unit" || continue
  if systemctl reload "$unit"; then
    main="$(systemctl show -p MainPID --value "$unit")"
  else
    echo "systemctl reload $unit failed" >&2
    failed=1
  fi
  break
done

# Every other listener, wherever it came from ("sshd: /usr/sbin/sshd -D
# [listener] ..." is what sshd calls its listening process; the per-login
# processes are named after the user). SIGHUP is the reload sshd itself
# knows: it starts over with the same sockets and a fresh configuration.
for pid in $(pgrep -f '^sshd: .*\[listener\]'); do
  [[ "$pid" == "$main" ]] && continue
  if ! kill -HUP "$pid" 2>/dev/null && kill -0 "$pid" 2>/dev/null; then
    echo "could not reload sshd process $pid" >&2
    failed=1
  fi
done

exit "$failed"
