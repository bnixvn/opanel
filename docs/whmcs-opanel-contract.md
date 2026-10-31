# WHMCS ↔ OPanel Provisioning API Contract

**Version**: 1.1.0  
**Base path**: `/api/provisioning/v1`  
**Auth**: Bearer token (API token, not user JWT)  
**Content-Type**: `application/json`

## Architecture

| System | Responsibility |
|--------|---------------|
| WHMCS | Billing, orders, invoices, service status, client management |
| OPanel | User accounts, websites, databases, SSL, quotas, runtime, SSO |

WHMCS never calls OPanel DB directly. All communication via provisioning API.

## Auth

API tokens stored in `api_tokens` table. Each token has:
- `name`, `token_hash` (SHA-256), `scopes` (JSON array), `expires_at`, `revoked_at`, `ip_allowlist`

Scopes: `provisioning:read`, `provisioning:write`, `provisioning:admin`

WHMCS sends token in `Authorization: Bearer <token>` header (from server `Access Hash` or `Password`).

## Response Format

Success responses return the **raw data object (or array) directly** — there is
**no** `{success, data, error}` envelope. `GET /plans` returns a JSON array;
every account endpoint returns a JSON object.

Error responses use FastAPI's default shape with a 4xx/5xx status:

```json
{
  "detail": "Human-readable error message"
}
```

A `422` (request-body validation) returns `detail` as an **array** of
field-error objects rather than a string. The WHMCS module reads `detail`
(falling back to the raw body) for the message, and tolerates — but does not
require — a legacy `{data: ...}` wrapper.

## Endpoints

### Plans

#### `GET /plans`

List the administrator's global hosting plans (`owner_id IS NULL`), used by the
WHMCS PackageLoader. Note: the list is **not** filtered by the plan's `active`
flag, so inactive plans are returned too; reseller-owned plans are excluded.

**Response**: Array of plan objects (raw array, no envelope).

```json
[
  {
    "id": 1,
    "slug": "starter",
    "name": "Starter",
    "website_limit": 1,
    "storage_limit_mb": 1024,
    "php_version": "8.4",
    "app_type": "wordpress",
    "auto_ssl": true,
    "active": true
  }
]
```

### Accounts

#### `GET /accounts/{external_id}`

Get account status. `external_id` format: `whmcs:{serviceid}`

**Response**:

```json
{
  "external_id": "whmcs:123",
  "user_id": 42,
  "username": "op_example_abc12345",
  "domain": "example.com",
  "status": "active",
  "plan_id": 1,
  "package_name": "Starter",
  "service_label": "example.com — Starter",
  "storage_used_bytes": 52428800,
  "storage_limit_bytes": 1073741824,
  "website_count": 1,
  "website_limit": 1,
  "created_at": "2026-08-09T00:00:00"
}
```

> `created_at` is a naive UTC timestamp from `datetime.isoformat()` — no `Z`
> suffix or offset.

#### `POST /accounts`

Create hosting account. **Idempotent** by `external_id`.

**Request**:

```json
{
  "external_id": "whmcs:123",
  "username": "op_example_abc12345",
  "password": "secure-password-here",
  "domain": "example.com",
  "package_id": 1,
  "php_version": "8.4",
  "app_type": "wordpress",
  "install_wordpress": true,
  "enable_ssl": true
}
```

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `external_id` | yes | — | Idempotency key |
| `username` | yes | — | Linux-safe, 3-32 chars |
| `password` | yes | — | 12-72 chars |
| `domain` | no | — | Primary domain |
| `package_id` | no | `1` | Maps to HostingPlan |
| `php_version` | no | `"8.4"` | |
| `app_type` | no | `"php"` | `php`, `wordpress`, `static` |
| `install_wordpress` | no | `false` | API accepts it; the WHMCS module never sends it |
| `enable_ssl` | no | `false` | API accepts it; the WHMCS module never sends it |

> The provisioning API still accepts `install_wordpress` and `enable_ssl`, but
> the bundled WHMCS module intentionally does **not** send them: a WHMCS order
> only creates the account and attaches its domain. WordPress and SSL are left
> for the customer to set up in the panel. Other API clients may still pass
> them.

**Idempotency**: If `external_id` already exists (and is not terminated), the
existing account is returned unchanged. The status code is **`201` either way** —
the endpoint is declared `status_code=201` unconditionally, so a replay does not
return `200`. The WHMCS module accepts any 2xx. A replay against a *suspended*
account returns it still suspended (create does not re-activate).

**Response**: Account object (same as GET).

#### `POST /accounts/{external_id}/suspend`

Suspend account. Disables panel user, locks Linux user, disables vhost.

**Request** (optional):

```json
{
  "reason": "Suspended by WHMCS"
}
```

**Response**: Account object with `status: "suspended"`.

#### `POST /accounts/{external_id}/unsuspend`

Unsuspend account. Re-enables everything.

**Response**: Account object with `status: "active"`.

#### `DELETE /accounts/{external_id}`

Terminate account. **Destructive and irreversible**: every website owned by the
account has its database dropped and its files deleted, then the Linux user and
its whole home directory are removed.

Takes no query parameters. There is no soft-delete mode -- an earlier draft of
this document described a `backup` parameter that the API never implemented, so
callers that sent it got a full teardown while believing data was kept.

Backups already written under `/var/backups/opanel` are outside the home
directory and survive, but only if backups were configured and had run. Do not
treat them as a rollback path for terminate.

**Response**: `{"terminated": true}`

#### `PATCH /accounts/{external_id}/password`

Change account password. Updates panel + Linux/SFTP.

**Request**:

```json
{
  "password": "new-secure-password"
}
```

**Response**: `{"changed": true}`

#### `PATCH /accounts/{external_id}/package`

Change hosting plan. Updates quota only, no recreate.

**Request**:

```json
{
  "package_id": 2
}
```

**Response**: Account object with new plan.

#### `GET /accounts/{external_id}/usage`

Get current resource usage.

**Response**:

```json
{
  "external_id": "whmcs:123",
  "storage_used_bytes": 52428800,
  "storage_limit_bytes": 1073741824,
  "storage_percent": 4.88,
  "website_count": 1,
  "website_limit": 1
}
```

#### `POST /accounts/{external_id}/login`

Generate a one-time SSO link. The token is single-use, 256-bit, and expires in
**300 seconds** (5 minutes) — the window must survive page-load-to-click time
because the WHMCS module bakes the URL into a rendered page rather than following
it at click time. Requires the `provisioning:write` scope.

**Response**:

```json
{
  "login_url": "https://panel.example.com/sso#<raw-token>",
  "expires_at": "2026-08-09T00:05:00"
}
```

The token rides in the URL **fragment** (`/sso#<token>`), not the path. The
module reads `login_url` and falls back to the plain panel URL if it is absent.

## Provisioning Behavior

### Create
1. Validate input (username, password, domain, package).
2. Look up `HostingPlan` by `package_id`.
3. If `external_id` already exists → return existing account (idempotent).
4. Create panel user (`site_users.ensure_panel_user`).
5. Create website (OpenLiteSpeed vhost).
6. If `install_wordpress` → install WP.
7. If `domain` → set up DNS-ready vhost.
8. If `enable_ssl` → request Let's Encrypt.
9. Create `HostingAccount` record.
10. Create `ProvisioningJob` with status `completed`.
11. If any step fails → set account status `failed`, job `failed`, return error.

**Account `status` values**: `provisioning` (create in progress), `active`,
`suspended`, `failed` (a provisioning step errored), `terminated`. The WHMCS
module passes `status` straight through to the client-area template and does not
map it to a WHMCS service status, so `provisioning`/`failed` can surface to the
customer verbatim.

### Suspend
1. Disable panel user (`is_active = false`).
2. Lock the Linux user via the privileged helper subcommand `panel-user-lock`
   (not a direct `usermod -L`).
3. Disable vhost / render suspend page.
4. Set account `status = "suspended"`.

If some vhosts cannot be toggled, the account status still changes but the
`ProvisioningJob` is marked `failed`.

### Unsuspend
1. Unlock the Linux user via the helper subcommand `panel-user-unlock`.
2. Enable panel user (`is_active = true`).
3. Restore vhost.
4. Set account `status = "active"`.

### Terminate
Full teardown, always. For each website owned by the account: drop its MariaDB
database and user, remove the vhost, delete the document root. Then delete the
panel user, the Linux/SFTP user, its crontab and `/home/<user>` entirely.

To stop service without destroying data, use **Suspend** instead -- that
disables the user, ends open sessions and takes the vhost down while leaving
files and databases in place.

### ChangePassword
- Update `User.hashed_password`.
- Update Linux/SFTP password via `chpasswd` or `usermod`.
- Do **not** log the password.

### ChangePackage
- Update `HostingAccount.plan_id`.
- Update `User.website_limit`, `User.storage_limit_mb` from plan.
- No recreate, no restart.

## Security

- API tokens hashed (SHA-256) in DB, never stored plaintext.
- Tokens have expiry + revocation.
- IP allowlist per token (optional).
- All provisioning actions logged to `provisioning_jobs` table.
- Passwords/SSO tokens never appear in logs.

## WHMCS Module

Located at `modules/servers/opanel/`:

```
opanel.php        — Module functions
hooks.php         — Service label hook
templates/
  clientarea.tpl  — Client area template
README.md
```

Module name: `opanel` (lowercase, WHMCS convention).  
Server type: `opanel`.  
Display name: `OPanel Hosting`.

Token source: `serveraccesshash` (primary) or `serverpassword` (fallback).
