#!/usr/bin/env bash
# phpMyAdmin itself, from upstream rather than apt (operator, 2026-10-18: "xài
# bản tải trực tiếp trước giờ vẫn login sso được mà" - the directly-downloaded
# build has always worked with single sign-on, so there is no reason to keep
# the Debian package's chain of esm-apps dependencies).
#
# apt's `phpmyadmin` package pulls apache2 (its only php-json provider in
# Debian's dependency graph is libapache2-mod-php) plus six php-symfony-*
# packages, php-twig and a dozen more php-composer-*/php-phpmyadmin-* bits --
# all needing Ubuntu Pro (esm-apps) for security fixes. Downloading the
# official tarball needs none of that: it ships its own PHP dependencies
# (vendor/), and OPanel already runs phpMyAdmin on OpenLiteSpeed + lsapi, never
# Apache. installer/files/opanel-packages.sh purges the apt package and
# whatever only it needed once this has put a working copy in its place.
#
# Run by install.sh and every update.sh; safe to run again -- it only
# downloads when the pinned version is not already in place.
set -euo pipefail

PHPMYADMIN_VERSION="5.2.3"
PMA_DIR="/usr/share/phpmyadmin"
PMA_MARKER="${PMA_DIR}/.opanel-version"
PMA_TEMP_DIR="/var/lib/phpmyadmin/tmp"
BASE_URL="https://files.phpmyadmin.net/phpMyAdmin/${PHPMYADMIN_VERSION}"
ARCHIVE="phpMyAdmin-${PHPMYADMIN_VERSION}-all-languages.tar.gz"

log() { echo "[phpmyadmin] $*"; }

if [[ -f "$PMA_MARKER" && "$(cat "$PMA_MARKER" 2>/dev/null)" == "$PHPMYADMIN_VERSION" ]]; then
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

log "Downloading phpMyAdmin ${PHPMYADMIN_VERSION}"
curl -fsSL --retry 3 --max-time 120 "${BASE_URL}/${ARCHIVE}" -o "${tmp}/${ARCHIVE}"
curl -fsSL --retry 3 --max-time 30 "${BASE_URL}/${ARCHIVE}.sha256" -o "${tmp}/${ARCHIVE}.sha256"

expected="$(awk '{print $1; exit}' "${tmp}/${ARCHIVE}.sha256")"
actual="$(sha256sum "${tmp}/${ARCHIVE}" | awk '{print $1}')"
if [[ -z "$expected" || "$expected" != "$actual" ]]; then
  log "the download does not match the checksum phpmyadmin.net published for ${PHPMYADMIN_VERSION}; not installed"
  exit 1
fi

tar -xzf "${tmp}/${ARCHIVE}" -C "$tmp"
extracted="${tmp}/phpMyAdmin-${PHPMYADMIN_VERSION}-all-languages"
[[ -d "$extracted" ]] || { log "the archive did not contain the folder expected"; exit 1; }

# The web setup wizard, test fixtures and build-time files: no business being
# reachable from a browser, or taking up space, on an install nobody builds.
rm -rf "${extracted}/setup" "${extracted}/examples" "${extracted}/test" \
       "${extracted}/.github" "${extracted}/package.json" "${extracted}/babel.config.json" \
       "${extracted}/.rtlcssrc.json" "${extracted}/yarn.lock" "${extracted}/composer.lock"

# phpMyAdmin reads its own docRoot's config.inc.php by default -- this makes
# that file a one-line pointer at the real configuration OPanel manages, so
# every other script that touches /etc/phpmyadmin (conf.d/opanel-signon.php,
# ownership, the sed-patches in refresh_tools_ols) keeps working unchanged.
cat >"${extracted}/config.inc.php" <<'PHP'
<?php
// OPanel: the real configuration lives in /etc/phpmyadmin, managed by the
// panel's installer and helper. See installer/files/opanel-phpmyadmin.sh.
require '/etc/phpmyadmin/config.inc.php';
PHP

chown -R root:root "$extracted"
find "$extracted" -type d -exec chmod 0755 {} +
find "$extracted" -type f -exec chmod 0644 {} +

rm -rf "${PMA_DIR}.old"
if [[ -d "$PMA_DIR" ]]; then
  mv "$PMA_DIR" "${PMA_DIR}.old"
fi
mv "$extracted" "$PMA_DIR"
rm -rf "${PMA_DIR}.old"
echo "$PHPMYADMIN_VERSION" >"$PMA_MARKER"

install -d -o www-data -g www-data -m 0770 "$PMA_TEMP_DIR"

install -d -m 0755 /etc/phpmyadmin/conf.d
if [[ ! -f /etc/phpmyadmin/config.inc.php ]]; then
  cat >/etc/phpmyadmin/config.inc.php <<'PHP'
<?php
// OPanel's own phpMyAdmin configuration (installer/files/opanel-phpmyadmin.sh).
// No phpMyAdmin configuration storage (pmadb): every account's databases are
// reached through the single sign-on in conf.d/opanel-signon.php, which this
// glob picks up -- bookmarks, relations and the rest of that storage were
// never configured here, on the Debian package either.
$cfg['UploadDir'] = '';
$cfg['SaveDir'] = '';
$cfg['TempDir'] = '/var/lib/phpmyadmin/tmp';

foreach (glob('/etc/phpmyadmin/conf.d/*.php') as $filename) {
    include $filename;
}
PHP
  chmod 0644 /etc/phpmyadmin/config.inc.php
fi

log "phpMyAdmin ${PHPMYADMIN_VERSION} installed"
