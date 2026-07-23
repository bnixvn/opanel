# OPanel

Lightweight hosting management panel for Ubuntu 24.04, powered by **OpenLiteSpeed** and **LSPHP**. OPanel helps you run WordPress and PHP websites from a single clean web UI with user ownership, quotas, backups, SSL, services, and firewall tools built in.

## Features

- Dashboard resource monitoring for CPU, RAM, disk, and network throughput
- WordPress one-click installer (LSPHP 8.3 / 8.4) with WP-CLI
- WordPress and PHP sites with editable OpenLiteSpeed vhost configs
- Panel users map to Linux/SFTP users; website source lives in `/home/<panel-user>/<domain>/public_html`
- Admin quick-login for creating sites as a selected user, plus one-owner assignment per website
- Website count limits and soft storage quotas per end user
- MariaDB database creation and management with phpMyAdmin SSO (60s tokens)
- Let's Encrypt SSL via certbot (webroot mode)
- Native file manager with upload, edit, archive, and extract support
- Backups: archive site files + SQL, scheduled full-user backups, restore, upload, download
- SFTP backup targets for off-server backup copies
- iptables + ipset firewall manager with protected panel/web/mail defaults, blocklists, and user rules
- Update controls for apt-based OS packages and OPanel source updates
- OpenLiteSpeed ModSecurity/WAF engine with lightweight WordPress/Laravel/PHP rules, per-site toggles, and HTTP Flood limits
- LSPHP config editor per version
- Cron job manager with whitelisted WP-CLI commands
- Role-based access: Admin / End user
- Google Authenticator compatible 2FA

## Tech stack

- **Backend:** Python 3, FastAPI, SQLAlchemy, SQLite (default), Pydantic v2, Jinja2
- **Frontend:** React 18, Vite, lucide-react
- **Webserver:** [OpenLiteSpeed](https://openlitespeed.org/) with LSPHP/LSAPI
- **PHP:** LSPHP 8.3 & 8.4 (LiteSpeed SAPI)
- **Database:** MariaDB
- **Cache:** Redis
- **Firewall:** iptables + ipset (chains: `OPANEL_INPUT`, `OPANEL_USER`, `OPANEL_BLOCKLIST`)
- **SSL:** Let's Encrypt via certbot (webroot)
- **WAF:** OpenLiteSpeed ModSecurity
- **SSH/SFTP:** OpenSSH
- **System:** systemd, Ubuntu 24.04 LTS

## Versioning

Current release: `1.0.46`.

OPanel versions use semantic versioning: `major.minor.patch`.

## System requirements

- Ubuntu 24.04 LTS (clean install recommended)
- Root access
- Optional: a domain pointing to the server's public IP (for SSL on the panel)
- 1 vCPU / 1 GB RAM minimum, 2 vCPU / 2 GB RAM recommended

## Fresh install

Run as root on a fresh Ubuntu 24.04 server.

### Quick install (recommended)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/bnixvn/opanel/main/installer/install.sh)
```

### Git clone install

```bash
set -e
apt-get update
apt-get install -y git
opanel_REPO=https://github.com/BNIX-VN/opanel.git
if [ -z "${opanel_VERSION:-}" ]; then
  opanel_VERSION="$(
    git ls-remote --tags --refs "${opanel_REPO}" 'refs/tags/v*' |
    awk -F/ '{print $NF}' |
    grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' |
    sort -V |
    tail -n 1
  )"
fi
test -n "${opanel_VERSION}" || { echo "Could not detect latest opanel release tag" >&2; exit 1; }
echo "Installing opanel ${opanel_VERSION}"
rm -rf /tmp/opanel-source
git clone --depth 1 --branch "${opanel_VERSION}" "${opanel_REPO}" /tmp/opanel-source
cd /tmp/opanel-source
trap 'cd /; rm -rf /tmp/opanel-source' EXIT
chmod +x installer/install.sh installer/update.sh installer/rescue-ufw-blocklist.sh
bash installer/install.sh
```

### Git clone install

```bash
set -e
apt-get update
apt-get install -y git
OPANEL_REPO=https://github.com/bnixvn/opanel.git
if [ -z "${OPANEL_VERSION:-}" ]; then
  OPANEL_VERSION="$(
    git ls-remote --tags --refs "${OPANEL_REPO}" 'refs/tags/v*' |
    awk -F/ '{print $NF}' |
    grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' |
    sort -V |
    tail -n 1
  )"
fi
test -n "${OPANEL_VERSION}" || { echo "Could not detect latest OPanel release tag" >&2; exit 1; }
echo "Installing OPanel ${OPANEL_VERSION}"
rm -rf /tmp/opanel-source
git clone --depth 1 --branch "${OPANEL_VERSION}" "${OPANEL_REPO}" /tmp/opanel-source
cd /tmp/opanel-source
trap 'cd /; rm -rf /tmp/opanel-source' EXIT
chmod +x installer/install.sh installer/update.sh
bash installer/install.sh
```

### Release zip install

```bash
apt-get update
apt-get install -y git curl unzip
OPANEL_REPO=https://github.com/bnixvn/opanel.git
if [ -z "${OPANEL_VERSION:-}" ]; then
  OPANEL_VERSION="$(
    git ls-remote --tags --refs "${OPANEL_REPO}" 'refs/tags/v*' |
    awk -F/ '{print $NF}' |
    grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' |
    sort -V |
    tail -n 1
  )"
fi
test -n "${OPANEL_VERSION}" || { echo "Could not detect latest OPanel release tag" >&2; exit 1; }
echo "Installing OPanel ${OPANEL_VERSION}"
rm -rf /opt/opanel-source /tmp/opanel-release /tmp/opanel-release.zip
curl -fL --connect-timeout 10 --max-time 300 \
  "https://github.com/bnixvn/opanel/archive/refs/tags/${OPANEL_VERSION}.zip" \
  -o /tmp/opanel-release.zip
unzip -q /tmp/opanel-release.zip -d /tmp/opanel-release
mv /tmp/opanel-release/opanel-* /opt/opanel-source
cd /opt/opanel-source
chmod +x installer/install.sh installer/update.sh
bash installer/install.sh
```

To pin a specific version, set `OPANEL_VERSION=v1.0.46` before running the script.

### What the installer does

1. Installs base packages: git, MariaDB, Redis, OpenSSH/SFTP, Node.js 22, certbot, phpMyAdmin, WP-CLI, iptables, ipset
2. Installs **OpenLiteSpeed** + **LSPHP 8.3/8.4** from the LiteSpeed repository
3. Copies source to `/opt/opanel`, builds the frontend, sets up the Python venv
4. Creates the `opanel` service account and the `admin` Linux/SFTP account
5. Creates the systemd service `opanel-api`
6. Configures phpMyAdmin SSO
7. Sets up iptables firewall with `OPANEL_INPUT`/`OPANEL_USER`/`OPANEL_BLOCKLIST` chains
8. Starts the panel on the configured port
9. Optionally issues Let's Encrypt SSL for the panel domain
10. Installs `/usr/local/sbin/opanel-update` for future updates

## Directory structure

| Path | Purpose |
|------|---------|
| `/opt/opanel/` | Application source + frontend |
| `/var/backups/opanel/` | Backup archives |
| `/var/lib/opanel/` | Runtime data (firewall rules, etc.) |
| `/usr/local/lsws/conf/opanel/` | OpenLiteSpeed vhost configs, SSL certs, ModSecurity rules |
| `/usr/local/lsws/lsphp83/` | LSPHP 8.3 binaries and config |
| `/usr/local/lsws/lsphp84/` | LSPHP 8.4 binaries and config |
| `/home/<user>/<domain>/public_html` | Website source files |

## Updating

```bash
opanel-update
```

Or manually:

```bash
cd /opt/opanel
git pull
bash installer/update.sh
```

## Firewall

OPanel uses **iptables + ipset** for firewall management:

- **Protected ports** (22, 80, 443, 465, 587, panel port) are always allowed
- **User rules** allow/block specific ports and IPs
- **Blocklists** via ipset sets (`opanel_blocklist4`, `opanel_blocklist6`) with auto-update from URL lists
- All rules persist in `/var/lib/opanel/firewall/rules.json`

## Webserver

OPanel uses **OpenLiteSpeed** as the webserver:

- Vhost configs stored in `/usr/local/lsws/conf/opanel/vhosts/`
- Custom directives supported per-site via the panel UI
- LSPHP replaces PHP-FPM — socket at `/tmp/lshttpd/lsphp{ver}.sock`
- LSCache built-in for WordPress sites
- ModSecurity/WAF per-site toggle with HTTP flood protection

## Development

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

## License

See [LICENSE](LICENSE) for details.
10. Print only the panel URL, user, and password; save the same fields to
    `/root/login.txt`.

You will be prompted for:

- Panel hostname (optional; blank uses the server IP)
- Panel port (default `2222`; UFW opens only the selected panel port)
- Whether to enable Let's Encrypt SSL for the panel domain
- An email for SSL registration

After install, open the `Panel URL` printed at the end of the installer. The
admin password is shown there and saved to `/root/login.txt`; store it in a
password manager.

## SSH rescue menu

Run as root:

```bash
opanel
```

Use this menu when the web panel is unavailable. It can show the saved login,
show rescue status, print recent logs, restart panel services, reopen required
firewall ports, reset the panel URL/port, repair panel SSL, fix runtime
permissions, change the `admin` password, and update opanel from the latest
release tag. Website and user management stays in the web panel.

## Updates

opanel can update itself from the latest stable GitHub release tag. Run it from
SSH:

```bash
opanel-update --release
```

The same action is available in the panel's **Updates** page. The update script
checks release tags, downloads the selected release zip to a temporary
directory, syncs source to `/opt/opanel`, rebuilds the frontend, refreshes
helper scripts, restarts the API, reloads Nginx, and removes the temporary
source. `/opt/opanel-source` is not kept for normal release updates; it is only a
developer `--branch` or `--skip-pull` source directory.

The panel stores release check and update progress in
`/var/lib/opanel/update-status.json`. The Updates page compares the installed
version with the newest release tag and enables the panel update button only
when a newer release is available.

To stay on a specific release:

```bash
opanel-update --tag v1.0.46
```

If the browser still shows the old UI, do a hard refresh (Ctrl + Shift + R) or
open in incognito.

## Project layout

```
opanel/
|-- backend/                    FastAPI application
|   |-- app/
|   |   |-- api/                  HTTP routes
|   |   |-- core/                 config, db, security, permissions, secrets
|   |   |-- models/               SQLAlchemy entities
|   |   |-- schemas/              Pydantic v2 schemas
|   |   |-- services/             nginx, mariadb, wp, firewall, backup, etc.
|   |   |-- templates/nginx/      Jinja2 vhost templates
|   |   |-- main.py
|   |   `-- seed.py               Seeds the first admin user
|   |-- tests/                   pytest smoke tests for validators
|   `-- requirements.txt
|-- frontend/                   React + Vite SPA
|   `-- src/
|-- installer/
|   |-- files/                   opanel-helper.sh + sudoers rule
|   |-- install.sh               Full first-time install
|   |-- rescue-ufw-blocklist.sh  Emergency UFW reset for oversized blocklists
|   `-- update.sh                Pull from GitHub and redeploy
`-- README.md
```

## Roles

| Role | Capabilities |
|------|--------------|
| `admin` | Full control: websites, users, ownership assignment, services, firewall, PHP config, backups, and security settings. |
| `end_user` | Manage only websites assigned to the account, including files, databases, SSL, WordPress tools, cron, and own backups. |

## User and website ownership

- Each panel user also has a Linux user with the same normalized username.
- The panel password is synced to the Linux password so the same account can
  log in with chrooted SFTP, for example `admin` -> `/home/admin`.
- Panel Linux users are members of `opanel-sftp`; the installer adds an SSHD
  `Match Group opanel-sftp` block for password-based SFTP access. SSH shells,
  TTYs and forwarding are disabled for these users.
- New websites are created under `/home/<panel-user>/<domain>/public_html`.
- If an admin creates a website without impersonating another user, the website
  belongs to the admin account.
- Admins can quick-login as another panel user before creating websites for
  that account.
- Admins can assign a website to exactly one panel user. Moving ownership also
  moves the site path to the new Linux user and rewrites the PHP-FPM/Nginx
  runtime configuration.
- Deleting a panel user permanently deletes all websites, files, databases,
  backup schedule links, cron entries, PHP-FPM pools, and Linux-user data owned
  by that user.

## Quotas

- End users have a website count limit and a storage limit in MB.
- Admin users are not storage-limited.
- Storage usage is calculated from all websites owned by the user.
- opanel enforces the storage limit before site creation, upload, edit, archive,
  extract, and ownership assignment operations.
- This is an application-level soft quota, not an OS disk quota.

## Configuration

`/opt/opanel/backend/.env` is generated by the installer and contains:

```ini
APP_ENV=production
SECRET_KEY=<random-32-bytes>
COMMAND_DRY_RUN=false
DATABASE_URL=sqlite:////opt/opanel/backend/opanel.db
REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_BACKEND=redis
ALLOWED_ORIGINS=https://panel.example.com
BACKUP_ROOT=/var/backups/opanel
SSL_EMAIL=admin@example.com
PANEL_URL=http://SERVER_IP:2222  # uses the selected panel port
PANEL_DOMAIN=
PANEL_PORT=2222                  # default; installer can set another port
PANEL_SSL_CERT=
PANEL_SSL_KEY=
FRONTEND_DIST=/opt/opanel/frontend/dist
```

### PHP-FPM auto tuning

opanel creates one PHP-FPM pool per managed PHP site. Pool sizing is tuned when
a site runtime is created or refreshed: the helper reads total RAM, CPU count,
and the number of managed PHP-FPM pools, then sets conservative `ondemand`
values for `pm.max_children`, idle timeout, request recycling, and hard request
timeout. Small VPS plans keep fewer children alive and recycle sooner; larger
plans receive a higher per-pool cap without using the same static values as a
1 GB server.

Optional overrides can be added to `/opt/opanel/backend/.env`:

```ini
opanel_PHP_FPM_WORKER_MB=128
opanel_PHP_FPM_MAX_CHILDREN=
opanel_PHP_FPM_IDLE_TIMEOUT=
opanel_PHP_FPM_MAX_REQUESTS=
opanel_PHP_FPM_REQUEST_TERMINATE_TIMEOUT=300
```

After changing overrides, retune existing pools:

```bash
sudo -u opanel env HOME=/opt/opanel sudo -n /usr/local/sbin/opanel-helper php-fpm-retune
```

### MariaDB auto tuning

opanel also writes `/etc/mysql/mariadb.conf.d/90-opanel-tuning.cnf` with VPS
sized MariaDB defaults. The helper tunes InnoDB buffer pool, connection count,
thread/table caches, temporary table limits, packet size, and slow-query logging
from total RAM and CPU count. The defaults leave memory for Nginx, PHP-FPM,
Redis, and the panel process instead of giving MariaDB a fixed oversized cache.

Optional overrides can be added to `/opt/opanel/backend/.env`:

```ini
opanel_MARIADB_BUFFER_POOL_SIZE=
opanel_MARIADB_MAX_CONNECTIONS=
opanel_MARIADB_THREAD_CACHE_SIZE=
opanel_MARIADB_TABLE_OPEN_CACHE=
opanel_MARIADB_TMP_TABLE_SIZE=
opanel_MARIADB_MAX_ALLOWED_PACKET=
opanel_MARIADB_LOG_FILE_SIZE=
opanel_MARIADB_IO_CAPACITY=
opanel_MARIADB_OPEN_FILES_LIMIT=
```

After changing overrides, retune MariaDB:

```bash
sudo -u opanel env HOME=/opt/opanel sudo -n /usr/local/sbin/opanel-helper mariadb-retune
```

The backend refuses to start in production with `COMMAND_DRY_RUN=true` or
`ALLOWED_ORIGINS=*`. SECRET_KEY must be at least 32 chars in production.

## Service commands

```bash
# API logs
journalctl -u opanel-api -f

# Restart the API after backend changes
systemctl restart opanel-api

# Reload Nginx after vhost edits
nginx -t && systemctl reload nginx

# Service status
systemctl status opanel-api nginx mariadb redis-server php8.3-fpm php8.4-fpm

# SSH rescue menu
opanel

# Change a cloned/template VM from old IP to the current/new IP
opanel change-ip

# Change the opanel admin login password
opanel change-admin-password

# Make opanel admin use the current root password after cloning a VPS/template
opanel sync-admin-root-password
```

## Security model

The panel daemon does **not** run as root. The installer creates a system user
`opanel` and a single root-owned helper script that does all privileged work.

```
opanel-api  (uvicorn, user=opanel, hardened systemd unit)
   |
   |  sudo -n /usr/local/sbin/opanel-helper <subcommand> ...
   v
opanel-helper  (root, runs only whitelisted operations)
```

What the helper allows:

- `systemctl start/stop/restart/reload <whitelisted service>`
- `nginx -t`, `nginx reload`
- `certbot --nginx ...` for a single validated domain
- create/delete panel Linux users, sync their SFTP password, and manage per-user PHP-FPM pools
- `ufw status/enable/disable/allow/deny/delete`
- fix ownership/ACLs for managed site paths under `/home/<panel-user>/<domain>`
- `rm -rf <managed site path>`
- WP-CLI and crontab management as the website's Linux user

Anything else is rejected. The helper validates domains, ports, IPs, and
filesystem paths before invoking the real binary.

The installer also creates a local MariaDB `opanel` account used by the API to
create per-site databases and users for WordPress installs.

Additional hardening on the systemd unit:

- Runs as `opanel` with only the `www-data` and `opanel-sites` supplementary groups.
  `opanel` is the service account for the API, not a panel login user; fresh
  installs do not create `/home/opanel` or `/home/opanel-sites`.
- Panel login users are Linux users in the `opanel-sftp` group. Their
  home directories live directly under `/home/<username>`, are root-owned
  SFTP chroots, and contain user-owned site directories with no `other`
  read/traverse permission. `/home` is executable-only for non-root users, so
  panel users cannot list other usernames.
- Uses `PrivateTmp`, `PrivateDevices`, `ProtectKernelTunables`,
  `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`,
  `ProtectClock`, `ProtectHostname`, and `ProtectProc=invisible`.
- Uses `RestrictNamespaces`, `RestrictRealtime`, `LockPersonality`,
  `MemoryDenyWriteExecute`, `SystemCallArchitectures=native`, and
  `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`.
- Drops ambient capabilities with `CapabilityBoundingSet=~`.

`NoNewPrivileges=false`, `ProtectSystem=false`, `ProtectHome=false`, and
`RestrictSUIDSGID=false` are intentional because the API must invoke the sudo
helper and manage website files under `/home`. Privileged operations stay
constrained by the root-owned helper and sudoers allowlist.

If the API itself were ever compromised, the attacker would be limited to:
- writing into `/etc/nginx/conf.d/`, managed site paths under `/home`, and `/var/backups/opanel/`
- running the helper subcommands above (no arbitrary code execution as root)

There is no path back to root via the API process.

## Security notes

- Login is rate-limited in Redis (8 attempts / minute, lockout after 20 fails),
  so counters are shared across uvicorn workers.
- Google Authenticator compatible TOTP 2FA can be enabled per account.
- Constant-time login path: bcrypt is verified even when the user does not
  exist, to avoid username enumeration via timing.
- DB and WordPress passwords are passed via stdin / `--prompt`, never as
  command-line args, so they don't appear in `ps`.
- DB passwords are encrypted at rest (Fernet, key derived from SECRET_KEY).
- Custom Nginx blocks are validated: braces must balance, dangerous directives
  (`server {`, `http {`, `events {`, `include`, `load_module`, `user`, `lua_*`,
  `proxy_pass`, `alias`, `*_log`, `ssl_*`) are rejected, max 16 KB.
- File manager rejects symlinks anywhere in the path. Website owners can manage
  their own deploy sources, including PHP, `.htaccess`, `.env`, and
  `wp-config.php`, with quota and ownership checks enforced by opanel.
- Path traversal is blocked at every layer that touches the filesystem.
- Auth uses HttpOnly cookies (`opanel_session`) plus a CSRF token cookie
  (`opanel_csrf`) echoed in the `X-CSRF-Token` header. The JWT is never
  exposed to JavaScript, mitigating token theft via XSS.
- Strict `Content-Security-Policy` (`script-src 'self'`, `frame-ancestors 'none'`).
- JWTs include a `jti`; revoked session IDs are stored server-side, and
  `token_version` invalidates previously issued JWTs on password change, role
  change, account disable, 2FA changes, or explicit logout.
- Production installs require `RATE_LIMIT_BACKEND=redis`, reject
  `ALLOWED_ORIGINS=*`, enforce `COMMAND_DRY_RUN=false`, and return generic
  500 responses for unhandled errors.

## License

MIT - see LICENSE.
