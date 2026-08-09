# WHMCS ↔ OPanel Provisioning API Contract

**Version**: 1.0.0  
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

## Response Envelope

All responses use:

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

Error responses (HTTP 4xx/5xx):

```json
{
  "success": false,
  "data": null,
  "error": "Human-readable error message"
}
```

## Endpoints

### Plans

#### `GET /plans`

List active hosting plans. Used by WHMCS PackageLoader.

**Response `data`**: Array of plan objects.

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

**Response `data`**:

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
  "created_at": "2026-08-09T00:00:00Z"
}
```

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
| `install_wordpress` | no | `false` | |
| `enable_ssl` | no | `false` | |

**Idempotency**: If `external_id` already exists, returns existing account (HTTP 200, not 201).

**Response `data`**: Account object (same as GET).

#### `POST /accounts/{external_id}/suspend`

Suspend account. Disables panel user, locks Linux user, disables vhost.

**Request** (optional):

```json
{
  "reason": "Suspended by WHMCS"
}
```

**Response `data`**: Account object with `status: "suspended"`.

#### `POST /accounts/{external_id}/unsuspend`

Unsuspend account. Re-enables everything.

**Response `data`**: Account object with `status: "active"`.

#### `DELETE /accounts/{external_id}?backup=true`

Terminate account. Default: soft delete (keep backups).

| Query param | Default | Notes |
|-------------|---------|-------|
| `backup` | `true` | `false` = hard delete |

**Response `data`**: `{"terminated": true}`

#### `PATCH /accounts/{external_id}/password`

Change account password. Updates panel + Linux/SFTP.

**Request**:

```json
{
  "password": "new-secure-password"
}
```

**Response `data`**: `{"changed": true}`

#### `PATCH /accounts/{external_id}/package`

Change hosting plan. Updates quota only, no recreate.

**Request**:

```json
{
  "package_id": 2
}
```

**Response `data`**: Account object with new plan.

#### `GET /accounts/{external_id}/usage`

Get current resource usage.

**Response `data`**:

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

Generate one-time SSO link. Token expires in 60 seconds.

**Response `data`**:

```json
{
  "login_url": "https://panel.example.com/sso/eyJ...",
  "expires_at": "2026-08-09T00:01:00Z"
}
```

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

### Suspend
1. Disable panel user (`is_active = false`).
2. Lock Linux user (`usermod -L`).
3. Disable vhost / render suspend page.
4. Set account `status = "suspended"`.

### Unsuspend
1. Unlock Linux user (`usermod -U`).
2. Enable panel user (`is_active = true`).
3. Restore vhost.
4. Set account `status = "active"`.

### Terminate
- **Soft** (default): disable user, remove vhost config, keep home dir + backups.
- **Hard** (`backup=false`): drop DB, delete home dir, remove all configs.

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
- Rate limiting on token-authenticated endpoints.

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
