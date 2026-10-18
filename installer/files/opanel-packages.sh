#!/usr/bin/env bash
# Packages the panel takes from upstream rather than Ubuntu's universe, and the
# universe ones it no longer installs. Run by install.sh and every update.sh;
# safe to run again.
#
# Operator, 2026-10-18 ("Kiểm tra lại bộ thư viện"): apt said composer, the
# php-symfony-* it pulls in, python3-pip and python3-wheel had security fixes
# waiting that only Ubuntu Pro (esm-apps) delivers. None of them is needed
# from apt:
#   - composer comes from getcomposer.org, its installer checked against the
#     published SHA-384, into /usr/local/bin, and self-updates on each run;
#     it runs on the system PHP (php-cli, from main, which Ubuntu patches);
#   - the panel's Python lives in its own venv, whose pip is upgraded there;
#     the system-wide pip and wheel were never used;
#   - phpMyAdmin comes from files/opanel-phpmyadmin.sh (upstream, checksummed)
#     instead of apt's `phpmyadmin`, which pulls apache2 (its only php-json
#     provider in Debian's graph is libapache2-mod-php) plus six
#     php-symfony-*, php-twig and a dozen php-composer-*/php-phpmyadmin-*
#     packages -- all esm-apps too. OpenLiteSpeed has always served it; Apache
#     never ran.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export COMPOSER_ALLOW_SUPERUSER=1

COMPOSER_BIN=/usr/local/bin/composer
# What composer, pip and apt's phpmyadmin brought in and nothing else needs
# once they are gone. Matched by name, so a package an administrator installed
# for a reason of their own is never swept up with them.
# php-mbstring deliberately stays out of this list even though phpmyadmin was
# its only apt-tracked dependent: WP-CLI runs under this same system php-cli
# (installer/files/opanel-helper.sh wp-site) and needs it. autoremove only
# sees apt's graph, not that, so the allowlist is what protects it.
LEFTOVERS_RE='^(composer|jsonlint|php-composer-[a-z-]+|php-symfony-[a-z0-9-]+|php-psr-[a-z-]+|php-react-promise|php-seld-[a-z-]+|php-json-schema|php-intl|php[0-9.]+-intl|python3-pip|python3-wheel|php-phpmyadmin-[a-z]+|php-twig(-i18n-extension)?|php-google-recaptcha|php-nikic-fast-route|php-slim-psr7|php-webmozart-assert|php-mariadb-mysql-kbs|libjs-[a-z0-9.-]+)$'

log() { echo "[packages] $*"; }

ensure_php_cli() {
  if ! dpkg -s php-cli >/dev/null 2>&1; then
    apt-get install -y php-cli >/dev/null
  fi
  # Kept for composer even when what first pulled it in is removed.
  apt-mark manual php-cli >/dev/null 2>&1 || true
}

install_composer() {
  if [[ -x "$COMPOSER_BIN" ]] && "$COMPOSER_BIN" --version 2>/dev/null | grep -q '^Composer'; then
    "$COMPOSER_BIN" self-update --stable --no-interaction --quiet 2>/dev/null \
      || log "composer self-update failed; keeping $("$COMPOSER_BIN" --version 2>/dev/null | head -n 1)"
    return 0
  fi
  local tmp expected actual
  tmp="$(mktemp -d)"
  curl -fsSL --retry 3 --max-time 60 https://composer.github.io/installer.sig -o "$tmp/installer.sig"
  curl -fsSL --retry 3 --max-time 60 https://getcomposer.org/installer -o "$tmp/composer-setup.php"
  expected="$(tr -d '[:space:]' < "$tmp/installer.sig")"
  actual="$(php -r 'echo hash_file("sha384", $argv[1]);' "$tmp/composer-setup.php")"
  if [[ -z "$expected" || "$expected" != "$actual" ]]; then
    rm -rf "$tmp"
    log "the composer installer does not match its published checksum; composer not installed"
    return 1
  fi
  php "$tmp/composer-setup.php" --quiet --install-dir=/usr/local/bin --filename=composer
  rm -rf "$tmp"
  chmod 0755 "$COMPOSER_BIN"
  log "installed $("$COMPOSER_BIN" --version 2>/dev/null | head -n 1)"
}

# Purge what is no longer wanted, and only that: the purge is simulated first
# and stops if apt would take anything not on the list with it.
purge_only() {
  local wanted=("$@") planned extra
  ((${#wanted[@]})) || return 0
  planned="$(apt-get -s purge "${wanted[@]}" 2>/dev/null | awk '/^Purg|^Remv/{print $2}' | sort -u)"
  extra="$(comm -23 <(printf '%s\n' "$planned") <(printf '%s\n' "${wanted[@]}" | sort -u) | grep -v '^$' || true)"
  if [[ -n "$extra" ]]; then
    log "not removing ${wanted[*]}: apt would also remove ${extra//$'\n'/ }"
    return 0
  fi
  apt-get purge -y "${wanted[@]}" >/dev/null
  log "removed ${wanted[*]}"
}

remove_universe_leftovers() {
  local remove=() name
  # apt's phpmyadmin is purged by opanel-phpmyadmin.sh itself, before it writes
  # anything of its own -- purging it only afterwards, here, once deleted files
  # by the path its own manifest tracks regardless of their new content.
  # The apt composer goes only once ours is in place.
  if dpkg -s composer >/dev/null 2>&1 && [[ -x "$COMPOSER_BIN" ]]; then remove+=(composer); fi
  for name in python3-pip python3-wheel; do
    dpkg -s "$name" >/dev/null 2>&1 && remove+=("$name")
  done
  purge_only "${remove[@]}"
  # Then what they had pulled in and nothing needs now.
  mapfile -t remove < <(apt-get -s autoremove 2>/dev/null | awk '/^Remv/{print $2}' | grep -E "$LEFTOVERS_RE" || true)
  purge_only "${remove[@]}"
}

# Apache came in only as apt's phpmyadmin's php-json provider (OpenLiteSpeed
# has always served phpMyAdmin itself; Apache never ran). Purged only once it
# is confirmed inactive -- a VPS where an administrator deliberately runs
# something else on it is left alone, same guard host_trim uses at install
# time. ssl-cert stays manual: its snakeoil certificate serves the tools port.
remove_apache_if_unused() {
  local purge=() pkg
  dpkg -s apache2 >/dev/null 2>&1 || return 0
  systemctl is-active --quiet apache2 2>/dev/null && return 0
  for pkg in apache2 apache2-bin apache2-data apache2-utils $(dpkg-query -W 'libapache2-mod-php*' 2>/dev/null | awk '{print $1}'); do
    dpkg -s "$pkg" >/dev/null 2>&1 && purge+=("$pkg")
  done
  ((${#purge[@]})) || return 0
  apt-mark manual ssl-cert >/dev/null 2>&1 || true
  purge_only "${purge[@]}"
}

# A PHP extension the panel removed before it purged them left its .ini behind
# (dpkg state "rc"), and LiteSpeed's PHP kept trying to load it.
purge_lsphp_leftover_configs() {
  local leftover=()
  mapfile -t leftover < <(dpkg -l 2>/dev/null | awk '$1 == "rc" && $2 ~ /^lsphp[0-9]+-[a-z0-9-]+$/ {print $2}')
  ((${#leftover[@]})) || return 0
  dpkg --purge "${leftover[@]}" >/dev/null
  log "purged the configuration left by ${leftover[*]}"
  systemctl restart lsws >/dev/null 2>&1 || true
}

ensure_php_cli
install_composer || true
remove_universe_leftovers
remove_apache_if_unused
purge_lsphp_leftover_configs
