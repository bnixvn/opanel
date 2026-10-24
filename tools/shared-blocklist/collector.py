#!/usr/bin/env python3
"""BNIX shared blocklist collector (blocklist.bnix.io.vn).

Panels (OPanel and BPanel, with the Shared blocklist addon on) report the
addresses their Fail2ban jails and WAF rules blocked. An address goes on the
public list once QUORUM different approved servers have reported it within
EXPIRY_DAYS, and comes off when none has for that long. Every panel with the
addon reads the list and blocks it with its firewall.

Trust is what makes a shared list safe to obey:
- A panel registers itself and gets a key, but its reports count only once
  the operator approves it on /admin. Anyone can install a panel from the
  public source; nobody can poison the list without the operator's say-so.
- One server alone never lists anybody (QUORUM), and a key that misbehaves
  is revoked, which drops what it reported.
- Never listed, whoever reports it: addresses that are not public
  (private, loopback, reserved), any reporting server's own address, the
  operator's allow list, and the published ranges of Cloudflare and of the
  Google and Bing crawlers.

Python standard library only (the host is the GitLab server, short of
memory), one SQLite file, behind GitLab's own nginx.
"""
import base64
import hmac
import html
import ipaddress
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LISTEN = os.environ.get("BLOCKLIST_LISTEN", "127.0.0.1:8790")
DATA = Path(os.environ.get("BLOCKLIST_DATA", "/var/lib/bnix-blocklist"))
DB_PATH = DATA / "collector.sqlite3"
PUBLIC_DIR = DATA / "public"
ADMIN_TOKEN_FILE = Path(os.environ.get("BLOCKLIST_ADMIN_TOKEN_FILE", "/etc/bnix-blocklist/admin-token"))

QUORUM = int(os.environ.get("BLOCKLIST_QUORUM", "2"))
EXPIRY_DAYS = int(os.environ.get("BLOCKLIST_EXPIRY_DAYS", "7"))
MAX_ITEMS_PER_REPORT = 500
MAX_ITEMS_PER_DAY = 20000
MAX_REGISTRATIONS_PER_IP_PER_DAY = 5
MAX_BODY = 256 * 1024
REBUILD_SECONDS = 60
RANGES_REFRESH_SECONDS = 24 * 3600
SOURCES = {
    # What a panel may say about an address; anything else is refused.
    "fail2ban", "waf",
}
PROTECTED_SOURCES = {
    "cloudflare-v4": "https://www.cloudflare.com/ips-v4/",
    "cloudflare-v6": "https://www.cloudflare.com/ips-v6/",
    "googlebot": "https://developers.google.com/static/search/apis/ipranges/googlebot.json",
    "google-special": "https://developers.google.com/static/search/apis/ipranges/special-crawlers.json",
    "bingbot": "https://www.bing.com/toolbox/bingbot.json",
}

_db_lock = threading.Lock()


def now() -> int:
    return int(time.time())


def iso(ts) -> str:
    return datetime.fromtimestamp(int(ts or 0), timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else ""


@contextmanager
def db():
    """One connection per use, closed after it; autocommit."""
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reporters (
            id INTEGER PRIMARY KEY,
            key_hash TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            hostname TEXT NOT NULL DEFAULT '',
            panel TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            last_seen_at INTEGER,
            day TEXT NOT NULL DEFAULT '',
            day_items INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS reports (
            reporter_id INTEGER NOT NULL REFERENCES reporters(id) ON DELETE CASCADE,
            ip TEXT NOT NULL,
            source TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            first_at INTEGER NOT NULL,
            last_at INTEGER NOT NULL,
            hits INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (reporter_id, ip)
        );
        CREATE INDEX IF NOT EXISTS reports_ip ON reports(ip);
        CREATE INDEX IF NOT EXISTS reports_last ON reports(last_at);
        CREATE TABLE IF NOT EXISTS allow (
            cidr TEXT PRIMARY KEY,
            note TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS protected (
            name TEXT PRIMARY KEY,
            cidrs TEXT NOT NULL,
            fetched_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS registrations (
            address TEXT NOT NULL,
            day TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (address, day)
        );
        """)


def hash_key(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()


# ----- what may never be listed ---------------------------------------------

def _networks(text_lines) -> list:
    nets = []
    for line in text_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            nets.append(ipaddress.ip_network(line, strict=False))
        except ValueError:
            continue
    return nets


def _fetch_protected(name: str, url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "bnix-blocklist/1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read(2 * 1024 * 1024).decode("utf-8", "replace")
    if url.endswith(".json"):
        data = json.loads(body)
        lines = [p.get("ipv4Prefix") or p.get("ipv6Prefix") or "" for p in data.get("prefixes", [])]
    else:
        lines = body.splitlines()
    nets = _networks(lines)
    if not nets:
        raise ValueError(f"{name}: no ranges in the answer")
    return [str(n) for n in nets]


def refresh_protected() -> None:
    for name, url in PROTECTED_SOURCES.items():
        try:
            cidrs = _fetch_protected(name, url)
        except Exception as exc:  # the last good copy stays in force
            print(f"protected ranges {name}: {exc}", file=sys.stderr)
            continue
        with _db_lock, db() as conn:
            conn.execute("INSERT INTO protected(name, cidrs, fetched_at) VALUES(?,?,?) "
                         "ON CONFLICT(name) DO UPDATE SET cidrs=excluded.cidrs, fetched_at=excluded.fetched_at",
                         (name, json.dumps(cidrs), now()))


def never_list(conn) -> list:
    nets = []
    for row in conn.execute("SELECT cidrs FROM protected"):
        nets += _networks(json.loads(row["cidrs"]))
    nets += _networks(row["cidr"] for row in conn.execute("SELECT cidr FROM allow"))
    nets += _networks(row["address"] for row in conn.execute("SELECT address FROM reporters WHERE address != ''"))
    return nets


def listable(ip, nets) -> bool:
    if not ip.is_global or ip.is_multicast:
        return False
    return not any(ip.version == net.version and ip in net for net in nets)


# ----- the public list ------------------------------------------------------

def build_list() -> dict:
    cutoff = now() - EXPIRY_DAYS * 86400
    with _db_lock, db() as conn:
        nets = never_list(conn)
        rows = conn.execute(
            "SELECT r.ip, COUNT(DISTINCT r.reporter_id) AS servers, MAX(r.last_at) AS last_at, "
            "GROUP_CONCAT(DISTINCT r.source) AS sources "
            "FROM reports r JOIN reporters p ON p.id = r.reporter_id "
            "WHERE p.status = 'approved' AND r.last_at >= ? GROUP BY r.ip HAVING servers >= ? "
            "ORDER BY servers DESC, last_at DESC", (cutoff, QUORUM)).fetchall()
        conn.execute("DELETE FROM reports WHERE last_at < ?", (cutoff - 30 * 86400,))
    listed = []
    for row in rows:
        try:
            ip = ipaddress.ip_address(row["ip"])
        except ValueError:
            continue
        if listable(ip, nets):
            listed.append((ip, row))
    listed.sort(key=lambda item: (item[0].version, item[0]))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = [
        "# BNIX shared blocklist - https://blocklist.bnix.io.vn/",
        f"# Generated {stamp}; {len(listed)} addresses reported by at least {QUORUM} servers "
        f"within {EXPIRY_DAYS} days.",
    ]
    text = "\n".join(header + [str(ip) for ip, _ in listed]) + "\n"
    tmp = PUBLIC_DIR / ".blocklist.txt.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, PUBLIC_DIR / "blocklist.txt")
    v4 = "\n".join(header + [str(ip) for ip, _ in listed if ip.version == 4]) + "\n"
    tmp = PUBLIC_DIR / ".blocklist-v4.txt.tmp"
    tmp.write_text(v4, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, PUBLIC_DIR / "blocklist-v4.txt")
    return {"count": len(listed), "generated_at": stamp}


def background() -> None:
    last_ranges = 0
    while True:
        try:
            if time.time() - last_ranges > RANGES_REFRESH_SECONDS:
                refresh_protected()
                last_ranges = time.time()
            build_list()
        except Exception as exc:
            print(f"background: {exc}", file=sys.stderr)
        time.sleep(REBUILD_SECONDS)


# ----- HTTP -----------------------------------------------------------------

class Reject(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def client_address(handler) -> str:
    # nginx in front passes the real client; nothing else reaches 127.0.0.1.
    forwarded = handler.headers.get("X-Real-IP", "").strip()
    return forwarded or handler.client_address[0]


def admin_token() -> str:
    try:
        return ADMIN_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def require_admin(handler) -> None:
    token = admin_token()
    given = handler.headers.get("Authorization", "")
    ok = False
    if token and given.startswith("Basic "):
        try:
            user, _, password = base64.b64decode(given[6:]).decode().partition(":")
            ok = user == "admin" and hmac.compare_digest(password, token)
        except Exception:
            ok = False
    if not ok:
        raise Reject(401, "admin login required")


def reporter_for(handler, conn):
    given = handler.headers.get("Authorization", "")
    if not given.startswith("Bearer "):
        raise Reject(401, "missing key")
    row = conn.execute("SELECT * FROM reporters WHERE key_hash = ?", (hash_key(given[7:].strip()),)).fetchone()
    if not row or row["status"] == "revoked":
        raise Reject(401, "unknown or revoked key")
    return row


class Handler(BaseHTTPRequestHandler):
    server_version = "bnix-blocklist/1"

    def log_message(self, fmt, *args):  # one line per request, without the key
        sys.stderr.write("%s %s\n" % (client_address(self), fmt % args))

    def _send(self, status, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise Reject(413, "body too large")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise Reject(400, "body is not JSON")

    def _dispatch(self, method):
        path = self.path.split("?", 1)[0]
        routes = {
            ("GET", "/v1/health"): self.health,
            ("POST", "/v1/register"): self.register,
            ("GET", "/v1/me"): self.me,
            ("POST", "/v1/report"): self.report,
            ("GET", "/admin"): self.admin_page,
            ("GET", "/admin/api/state"): self.admin_state,
            ("POST", "/admin/api/reporter"): self.admin_reporter,
            ("POST", "/admin/api/allow"): self.admin_allow,
        }
        handler = routes.get((method, path))
        try:
            if not handler:
                raise Reject(404, "not found")
            handler()
        except Reject as exc:
            extra = {"WWW-Authenticate": 'Basic realm="BNIX blocklist"'} if exc.status == 401 and path.startswith("/admin") else None
            self._send(exc.status, {"detail": exc.message}, extra=extra)
        except Exception as exc:  # pragma: no cover
            print(f"error {path}: {exc!r}", file=sys.stderr)
            self._send(500, {"detail": "internal error"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    # -- panels --

    def health(self):
        self._send(200, {"ok": True})

    def register(self):
        body = self._json()
        address = client_address(self)
        day = time.strftime("%Y-%m-%d", time.gmtime())
        key = "bbl_" + secrets.token_urlsafe(32)
        with _db_lock, db() as conn:
            count = conn.execute("SELECT count FROM registrations WHERE address=? AND day=?", (address, day)).fetchone()
            if count and count["count"] >= MAX_REGISTRATIONS_PER_IP_PER_DAY:
                raise Reject(429, "too many registrations from this address today")
            conn.execute("INSERT INTO registrations(address, day, count) VALUES(?,?,1) "
                         "ON CONFLICT(address, day) DO UPDATE SET count = count + 1", (address, day))
            cur = conn.execute(
                "INSERT INTO reporters(key_hash, hostname, panel, version, address, created_at) VALUES(?,?,?,?,?,?)",
                (hash_key(key), str(body.get("hostname", ""))[:120], str(body.get("panel", ""))[:20],
                 str(body.get("version", ""))[:20], address, now()))
        self._send(201, {"id": cur.lastrowid, "key": key, "status": "pending",
                         "detail": "Registered. Reports count once the operator approves this server."})

    def me(self):
        with _db_lock, db() as conn:
            row = reporter_for(self, conn)
            conn.execute("UPDATE reporters SET last_seen_at=? WHERE id=?", (now(), row["id"]))
        self._send(200, {"id": row["id"], "status": row["status"], "quorum": QUORUM, "expiry_days": EXPIRY_DAYS})

    def report(self):
        body = self._json()
        items = body.get("items")
        if not isinstance(items, list):
            raise Reject(400, "items must be a list")
        if len(items) > MAX_ITEMS_PER_REPORT:
            raise Reject(413, f"at most {MAX_ITEMS_PER_REPORT} items per report")
        stamp = now()
        day = time.strftime("%Y-%m-%d", time.gmtime())
        accepted = 0
        with _db_lock, db() as conn:
            row = reporter_for(self, conn)
            if row["status"] != "approved":
                conn.execute("UPDATE reporters SET last_seen_at=? WHERE id=?", (stamp, row["id"]))
                raise Reject(403, "this server is waiting for the operator's approval")
            used = row["day_items"] if row["day"] == day else 0
            if used + len(items) > MAX_ITEMS_PER_DAY:
                raise Reject(429, "daily report limit reached")
            for item in items:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source", "")).split(":", 1)[0]
                if source not in SOURCES:
                    continue
                try:
                    ip = ipaddress.ip_address(str(item.get("ip", "")).strip())
                except ValueError:
                    continue
                if not ip.is_global:
                    continue
                detail = str(item.get("source", ""))[:60]
                conn.execute(
                    "INSERT INTO reports(reporter_id, ip, source, detail, first_at, last_at, hits) VALUES(?,?,?,?,?,?,1) "
                    "ON CONFLICT(reporter_id, ip) DO UPDATE SET last_at=excluded.last_at, hits=hits+1, "
                    "source=excluded.source, detail=excluded.detail",
                    (row["id"], str(ip), source, detail, stamp, stamp))
                accepted += 1
            conn.execute("UPDATE reporters SET last_seen_at=?, day=?, day_items=? WHERE id=?",
                         (stamp, day, used + len(items), row["id"]))
        self._send(200, {"accepted": accepted})

    # -- the operator --

    def admin_page(self):
        require_admin(self)
        self._send(200, ADMIN_HTML, ctype="text/html", extra={
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'"})

    def admin_state(self):
        require_admin(self)
        cutoff = now() - EXPIRY_DAYS * 86400
        with _db_lock, db() as conn:
            reporters = [dict(r) for r in conn.execute(
                "SELECT p.id, p.status, p.hostname, p.panel, p.version, p.address, p.created_at, p.last_seen_at, "
                "(SELECT COUNT(*) FROM reports r WHERE r.reporter_id = p.id AND r.last_at >= ?) AS recent "
                "FROM reporters p WHERE p.status != 'revoked' ORDER BY p.status = 'pending' DESC, p.id DESC", (cutoff,))]
            top = [dict(r) for r in conn.execute(
                "SELECT r.ip, COUNT(DISTINCT r.reporter_id) AS servers, MAX(r.last_at) AS last_at, "
                "GROUP_CONCAT(DISTINCT r.detail) AS sources FROM reports r JOIN reporters p ON p.id = r.reporter_id "
                "WHERE p.status = 'approved' AND r.last_at >= ? GROUP BY r.ip ORDER BY servers DESC, last_at DESC LIMIT 100",
                (cutoff,))]
            allow = [dict(r) for r in conn.execute("SELECT cidr, note, created_at FROM allow ORDER BY created_at DESC")]
        try:
            listed = sum(1 for line in (PUBLIC_DIR / "blocklist.txt").read_text().splitlines() if line and not line.startswith("#"))
        except OSError:
            listed = 0
        for row in reporters:
            row["created"] = iso(row.pop("created_at"))
            row["last_seen"] = iso(row.pop("last_seen_at"))
        for row in top:
            row["last"] = iso(row.pop("last_at"))
        self._send(200, {"reporters": reporters, "top": top, "allow": allow, "listed": listed,
                         "quorum": QUORUM, "expiry_days": EXPIRY_DAYS})

    def _admin_post(self):
        require_admin(self)
        # A form on another site cannot set this header, so no CSRF.
        if self.headers.get("X-Requested-With") != "bnix-admin":
            raise Reject(403, "missing X-Requested-With")
        return self._json()

    def admin_reporter(self):
        body = self._admin_post()
        action = body.get("action")
        if action not in {"approve", "revoke"}:
            raise Reject(400, "action must be approve or revoke")
        with _db_lock, db() as conn:
            if action == "approve":
                conn.execute("UPDATE reporters SET status='approved' WHERE id=? AND status='pending'", (int(body.get("id", 0)),))
            else:
                # Revoked: the key stops working and what it reported is gone.
                conn.execute("DELETE FROM reports WHERE reporter_id=?", (int(body.get("id", 0)),))
                conn.execute("UPDATE reporters SET status='revoked' WHERE id=?", (int(body.get("id", 0)),))
        build_list()
        self._send(200, {"ok": True})

    def admin_allow(self):
        body = self._admin_post()
        cidr = str(body.get("cidr", "")).strip()
        try:
            net = str(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            raise Reject(400, "not an address or range")
        with _db_lock, db() as conn:
            if body.get("remove"):
                conn.execute("DELETE FROM allow WHERE cidr=?", (net,))
            else:
                conn.execute("INSERT OR REPLACE INTO allow(cidr, note, created_at) VALUES(?,?,?)",
                             (net, str(body.get("note", ""))[:120], now()))
        build_list()
        self._send(200, {"ok": True})


ADMIN_HTML = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BNIX blocklist</title>
<style>
:root{--bg:#f5f7fa;--surface:#fff;--border:#dde2e9;--text:#111a2b;--muted:#4b5566;--primary:#1660cf;--ok:#087a5b;--warn:#8a5a00;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#0c1220;--surface:#121923;--border:#263041;--text:#e8edf5;--muted:#b4bfce;--primary:#5b9bff;--ok:#4cc79b;--warn:#f2c14e;--bad:#ff8a80}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:24px;margin:8px 0 4px}h2{font-size:18px;margin:24px 0 8px}
.muted{color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:4px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:last-child td{border-bottom:0}th{font-size:13px;color:var(--muted)}
button{font:inherit;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:6px;padding:4px 10px;cursor:pointer}
button.primary{background:var(--primary);border-color:var(--primary);color:#fff}
.pending{color:var(--warn);font-weight:600}.approved{color:var(--ok);font-weight:600}
form{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}input{font:inherit;padding:5px 8px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text)}
</style></head><body><main>
<h1>BNIX shared blocklist</h1>
<p class="muted" id="summary">Đang tải…</p>
<h2>Server báo cáo</h2><div class="card"><table><thead><tr><th>#</th><th>Trạng thái</th><th>Server</th><th>Panel</th><th>IP</th><th>Đăng ký</th><th>Lần cuối</th><th>Đã báo</th><th></th></tr></thead><tbody id="reporters"></tbody></table></div>
<h2>Không bao giờ chặn</h2>
<form id="allow-form"><input name="cidr" placeholder="IP hoặc dải, ví dụ 203.0.113.0/24" required><input name="note" placeholder="Ghi chú"><button class="primary">Thêm</button></form>
<div class="card"><table><thead><tr><th>Dải</th><th>Ghi chú</th><th></th></tr></thead><tbody id="allow"></tbody></table></div>
<h2>IP bị báo nhiều nhất</h2><div class="card"><table><thead><tr><th>IP</th><th>Số server</th><th>Nguồn</th><th>Lần cuối</th></tr></thead><tbody id="top"></tbody></table></div>
</main><script>
const $ = id => document.getElementById(id);
const cell = (tr, text, cls) => { const td = document.createElement('td'); td.textContent = text ?? ''; if (cls) td.className = cls; tr.append(td); return td; };
const post = (path, body) => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'bnix-admin' }, body: JSON.stringify(body) })
  .then(r => r.ok ? load() : r.json().then(d => alert(d.detail)));
async function load() {
  const s = await (await fetch('/admin/api/state')).json();
  $('summary').textContent = `${s.listed} IP đang trong danh sách · một IP cần ${s.quorum} server cùng báo trong ${s.expiry_days} ngày · danh sách: https://blocklist.bnix.io.vn/v1/blocklist.txt`;
  $('reporters').replaceChildren(...s.reporters.map(r => { const tr = document.createElement('tr');
    cell(tr, r.id); cell(tr, r.status === 'pending' ? 'Chờ duyệt' : 'Đã duyệt', r.status); cell(tr, r.hostname); cell(tr, `${r.panel} ${r.version}`);
    cell(tr, r.address); cell(tr, r.created); cell(tr, r.last_seen); cell(tr, r.recent);
    const td = cell(tr, '');
    if (r.status === 'pending') { const b = document.createElement('button'); b.className = 'primary'; b.textContent = 'Duyệt'; b.onclick = () => post('/admin/api/reporter', { id: r.id, action: 'approve' }); td.append(b, ' '); }
    const x = document.createElement('button'); x.textContent = 'Thu hồi'; x.onclick = () => confirm(`Thu hồi ${r.hostname}? Những IP nó đã báo sẽ bị xoá.`) && post('/admin/api/reporter', { id: r.id, action: 'revoke' }); td.append(x);
    return tr; }));
  $('allow').replaceChildren(...s.allow.map(a => { const tr = document.createElement('tr'); cell(tr, a.cidr); cell(tr, a.note);
    const b = document.createElement('button'); b.textContent = 'Xoá'; b.onclick = () => post('/admin/api/allow', { cidr: a.cidr, remove: true }); cell(tr, '').append(b); return tr; }));
  $('top').replaceChildren(...s.top.map(t => { const tr = document.createElement('tr'); cell(tr, t.ip); cell(tr, t.servers); cell(tr, t.sources); cell(tr, t.last); return tr; }));
}
$('allow-form').onsubmit = e => { e.preventDefault(); const f = new FormData(e.target); post('/admin/api/allow', { cidr: f.get('cidr'), note: f.get('note') }); e.target.reset(); };
load();
</script></body></html>"""


def main() -> None:
    init_db()
    threading.Thread(target=background, daemon=True).start()
    host, _, port = LISTEN.rpartition(":")
    server = ThreadingHTTPServer((host, int(port)), Handler)
    print(f"bnix-blocklist listening on {LISTEN}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
