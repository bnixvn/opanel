# BNIX shared blocklist collector

`https://blocklist.bnix.io.vn`, on the GitLab server (163.61.72.156), behind
GitLab's bundled nginx. Panels with the **Shared blocklist** addon (OPanel and
BPanel) report the addresses their Fail2ban jails banned and their WAF denied
on attack-only rules, and read the list back through the firewall's URL
blocklists.

## How an address gets listed

- A panel registers itself and gets a key. Its reports count once it is
  **approved** on `/admin`.
- An address is listed when **2** approved servers reported it within
  **7 days**. It comes off once none has done so for 7 days.
- These are never listed:
  - addresses that are not public;
  - any registered server's own address;
  - the admin allow list;
  - Cloudflare, Googlebot, Google special crawlers and Bingbot. These ranges
    are fetched daily, and the last good copy is kept.
- Revoking a server deletes everything it reported.
- Limits:
  - 500 items per report;
  - 20000 items per server per day;
  - 5 registrations per address per day;
  - nginx rate-limits `/v1/register` and `/v1/`.

## Endpoints

| | |
|---|---|
| `GET /v1/blocklist.txt` | The list: one address per line, `#` comments. A static file, rebuilt every minute. |
| `GET /v1/blocklist-v4.txt` | IPv4 only. |
| `POST /v1/register` | `{hostname, panel, version}` → `{id, key, status}`. |
| `GET /v1/me` | `Bearer` key → `{status}` (`pending` / `approved`). |
| `POST /v1/report` | `Bearer` key, `{items: [{ip, source}]}`. `source` is `fail2ban:<jail>` or `waf:<rule id>`. |
| `/admin` | Approve or revoke servers, the allow list, and the most-reported addresses. Basic auth with user `admin`. |

The admin password is in `/etc/bnix-blocklist/admin-token` on the server:
`sudo cat /etc/bnix-blocklist/admin-token`.

## Files on the server

| | |
|---|---|
| `/opt/bnix-blocklist/collector.py` | This directory's `collector.py`. Python standard library only. |
| `/etc/systemd/system/bnix-blocklist.service` | `bnix-blocklist.service`. It runs as `bnix-blocklist` with MemoryMax 192M. |
| `/var/lib/bnix-blocklist/` | `collector.sqlite3` and `public/` (the list files nginx serves). |
| `/etc/gitlab/nginx-custom/blocklist.conf` | `nginx-blocklist.conf`. It is included by `nginx['custom_nginx_config']` in `/etc/gitlab/gitlab.rb`. |
| `/etc/letsencrypt/live/blocklist.bnix.io.vn/` | The certbot (webroot) certificate. The deploy hook runs `gitlab-ctl hup nginx`. |

### Updating the collector

```
scp collector.py root@163.61.72.156:/opt/bnix-blocklist/collector.py
ssh root@163.61.72.156 systemctl restart bnix-blocklist
```

`gitlab-ctl reconfigure` keeps the include, because it is in `gitlab.rb`.
