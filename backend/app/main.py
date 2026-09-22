import logging
import mimetypes
import os
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Depends, Request, Response, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.api import api_tokens, auth, databases, firewall, maintenance, panel_settings as panel_settings_api, plans, provisioning, services, terminal, updates, users, waf, websites
from app.core.config import settings
from app.core.database import get_db, run_migrations
from app.core.version import APP_VERSION
from app.services import panel_settings as panel_brand_settings

run_migrations()

# Secure default umask: files get 644 (-rw-r--r--), dirs get 755 (rwxr-xr-x)
os.umask(0o022)

logger = logging.getLogger("OPanel")

app = FastAPI(title="OPanel API", version=APP_VERSION)

# Refuse to start in production with unsafe defaults.
if settings.app_env.lower() == "production":
    if settings.command_dry_run:
        raise RuntimeError(
            "COMMAND_DRY_RUN must be False in production. "
            "Set COMMAND_DRY_RUN=false in the environment."
        )

cors_origins = settings.cors_origins
if not cors_origins and settings.app_env != "production":
    cors_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)

# Compress responses (the ~1.8 MB JS bundle drops to ~490 KB, JSON lists shrink
# too). Matters most for admins reaching the panel over a slow/long-haul link.
app.add_middleware(GZipMiddleware, minimum_size=700, compresslevel=6)


def _is_potentially_trustworthy_origin(request) -> bool:
    host = (request.url.hostname or "").lower()
    return request.url.scheme == "https" or host in {"localhost", "127.0.0.1", "::1"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("Unhandled request error: %s %s", request.method, request.url.path)
    if settings.app_env.lower() == "production":
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if _is_potentially_trustworthy_origin(request):
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "accelerometer=(), autoplay=(), camera=(), display-capture=(), encrypted-media=(), "
        "fullscreen=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), midi=(), "
        "payment=(), usb=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    if settings.app_env.lower() == "production" and request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(websites.router, prefix="/api")
app.include_router(databases.router, prefix="/api")
app.include_router(firewall.router, prefix="/api")
app.include_router(services.router, prefix="/api")
app.include_router(updates.router, prefix="/api")
app.include_router(waf.router, prefix="/api")
app.include_router(maintenance.router, prefix="/api")
app.include_router(panel_settings_api.router, prefix="/api")
app.include_router(terminal.router, prefix="/api")
app.include_router(provisioning.router, prefix="/api")
app.include_router(api_tokens.router, prefix="/api")
app.include_router(plans.router, prefix="/api")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "name": panel_brand_settings.current_settings().get("app_name") or "OPanel",
        "version": APP_VERSION,
    }


frontend_dist = Path(settings.frontend_dist)
assets_dir = frontend_dist / "assets"
# The bundle ships self-hosted Lexend woff2 files under /assets (the panel's
# CSP is font-src 'self' data:, so a font CDN is not an option). Not every
# host has .woff2 in its mime database, and StaticFiles would then serve it
# as text/plain, so register it explicitly.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")


class _ImmutableStaticFiles(StaticFiles):
    """Vite emits content-hashed filenames under /assets, so a cache entry is
    valid forever — tell the browser not to revalidate on every page load."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code < 400:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if assets_dir.exists():
    app.mount("/assets", _ImmutableStaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/favicon.png", include_in_schema=False)
def favicon():
    custom = panel_brand_settings.current_settings().get("favicon_url") or ""
    if custom.startswith("/brand-assets/"):
        filename = custom.split("/brand-assets/", 1)[1].split("?", 1)[0]
        path, media_type = panel_brand_settings.asset_path(filename)
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    path = frontend_dist / "favicon.png"
    if path.exists():
        return FileResponse(
            path,
            media_type="image/png",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    raise HTTPException(status_code=404, detail="Not found")


@app.get("/brand-assets/{filename}", include_in_schema=False)
def brand_asset(filename: str):
    path, media_type = panel_brand_settings.asset_path(filename)
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


SSO_NONCE_COOKIE = "opanel_sso_nonce"


def _sso_page_html(nonce: str) -> str:
    """The sign-in confirmation page.

    The token still travels in the URL fragment so it never reaches an access
    log, and the script still lives at /sso.js because the panel's CSP sends
    script-src 'self' with no 'unsafe-inline'.

    What changed is that the page no longer submits by itself. An auto-submit
    made the whole handshake reachable by redirect: a page on any origin could
    send a victim to /sso#<the attacker's token> and the browser would complete
    the login unattended, leaving the victim working inside the attacker's
    panel. A form the visitor has to click cannot be driven that way, and
    naming the account on the button is what lets someone notice they were
    handed a link to a stranger's account.
    """
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>OPanel sign-in</title>
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;display:flex;justify-content:center;align-items:center;min-height:100vh;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#0f1115;color:#e8e6e3">
<main style="max-width:26rem;padding:2rem;text-align:center">
<h1 style="font-size:1.1rem;font-weight:600;margin:0 0 1rem">OPanel sign-in</h1>
<p id="sso-status" style="color:#a8a096;line-height:1.5;margin:0 0 1.25rem">Checking your sign-in link&hellip;</p>
<form id="sso-form" method="POST" action="/sso">
<input type="hidden" name="token" id="sso-token">
<input type="hidden" name="nonce" value="{nonce}">
</form>
<button id="sso-go" type="button" hidden
 style="font:inherit;font-weight:600;padding:.7rem 1.4rem;border:0;border-radius:8px;background:#2563eb;color:#fff;cursor:pointer"></button>
<p id="sso-note" style="color:#7d766c;font-size:.85rem;line-height:1.5;margin:1.25rem 0 0" hidden>
This replaces any OPanel session already open in this browser.</p>
</main>
<script src="/sso.js"></script>
</body></html>"""


@app.get("/sso", include_in_schema=False)
def sso_intermediary(request: Request):
    """Serve the sign-in confirmation page and mint its form nonce."""
    from app.api.auth import _is_secure_request
    from fastapi.responses import HTMLResponse

    nonce = secrets.token_urlsafe(32)
    page = HTMLResponse(
        _sso_page_html(nonce),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )
    # Double-submit pair for the POST below. It does not stop the redirect
    # attack on its own -- both halves of that happen on the panel's own origin
    # -- but it does stop a form on another origin posting straight to /sso and
    # skipping this page entirely.
    page.set_cookie(
        SSO_NONCE_COOKIE, nonce,
        max_age=600, httponly=True, samesite="strict",
        secure=_is_secure_request(request), path="/sso",
    )
    return page


@app.get("/sso.js", include_in_schema=False)
def sso_intermediary_script():
    """Same-origin script for the sign-in page (CSP is script-src 'self')."""
    from fastapi.responses import Response

    return Response(
        content="""(function(){
  var hash = window.location.hash.replace(/^#/, "");
  var status = document.getElementById("sso-status");
  var go = document.getElementById("sso-go");
  var note = document.getElementById("sso-note");
  if (!hash) { status.textContent = "This sign-in link is missing its token."; return; }
  document.getElementById("sso-token").value = hash;
  fetch("/sso/preview", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: hash })
  }).then(function (r) { return r.json(); }).then(function (d) {
    if (!d || !d.username) {
      status.textContent = "This sign-in link is not valid, has expired, or has already been used.";
      return;
    }
    status.textContent = "You are about to sign in to OPanel as:";
    // textContent, never innerHTML: the username is panel data, and this page
    // runs before any session exists.
    go.textContent = "Continue as " + d.username;
    go.hidden = false;
    note.hidden = false;
    go.addEventListener("click", function () {
      go.disabled = true;
      document.getElementById("sso-form").submit();
    });
  }).catch(function () {
    status.textContent = "Could not check this sign-in link.";
  });
})();
""",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.post("/sso/preview", include_in_schema=False)
def sso_preview(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Name the account a token belongs to, without consuming it.

    Whoever holds the token can already sign in as that account, so this
    discloses nothing they could not obtain by using it -- and it is what makes
    the confirmation meaningful.
    """
    from app.models.entities import User
    from app.services.provisioning import peek_sso_token

    token = (payload or {}).get("token") or ""
    if not isinstance(token, str) or not token:
        return {"username": None}
    user_id = peek_sso_token(db, token)
    if user_id is None:
        return {"username": None}
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    return {"username": user.username if user else None}


def _reject_cross_site_sso(request: Request) -> None:
    """Refuse an SSO POST initiated by another origin.

    Defence in depth only, and deliberately narrow: the attack this route
    needed protecting from is a redirect to /sso#<token>, where the form post
    that follows is same-origin by construction. That one is stopped by making
    the page require a click, not by this. What this does stop is a form on
    another site posting straight here and skipping the confirmation.

    Sec-Fetch-Site is sent by every current browser and is not settable from
    script. A non-browser client omits it and is unaffected.
    """
    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site and fetch_site not in {"same-origin", "same-site"}:
        raise HTTPException(status_code=403, detail="Cross-site SSO is not allowed")


def _sso_user_or_error(db: Session, token: str):
    """Consume the token and return the account, or a RedirectResponse."""
    from app.models.entities import User
    from app.services.provisioning import consume_sso_token

    user_id = consume_sso_token(db, token)
    if user_id is None:
        return RedirectResponse(url="/?sso=invalid", status_code=302)
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        return RedirectResponse(url="/?sso=invalid", status_code=302)
    # An SSO link must not be a way around two-factor authentication. The
    # password path enforces user.totp_enabled and impersonation re-prompts the
    # admin's own code; these routes went straight from consume_sso_token to
    # _issue_login_session, so an account that had deliberately turned 2FA on
    # still had a parallel door at lower assurance.
    #
    # Sent to the login form rather than answered with a raw JSON 403: there is
    # no exception handler for HTTPException here, so a customer clicking
    # "Log in to OPanel" in WHMCS would have been shown a bare error blob in a
    # new tab with nowhere to go.
    if getattr(user, "totp_enabled", False):
        return RedirectResponse(url="/?sso=2fa", status_code=302)
    return user


@app.post("/sso", include_in_schema=False)
def sso_login_post(
    request: Request,
    token: str = Form(...),
    nonce: str = Form(""),
    db: Session = Depends(get_db),
):
    """Consume a one-time SSO token submitted from the confirmation page."""
    from app.api.auth import _issue_login_session

    _reject_cross_site_sso(request)
    cookie_nonce = request.cookies.get(SSO_NONCE_COOKIE) or ""
    if not cookie_nonce or not secrets.compare_digest(cookie_nonce, nonce or ""):
        return RedirectResponse(url="/?sso=expired", status_code=302)

    result = _sso_user_or_error(db, token)
    if isinstance(result, RedirectResponse):
        result.delete_cookie(SSO_NONCE_COOKIE, path="/sso")
        return result

    redirect = RedirectResponse(url="/", status_code=302)
    _issue_login_session(redirect, request, result)
    redirect.delete_cookie(SSO_NONCE_COOKIE, path="/sso")
    return redirect


@app.get("/sso/{token}", include_in_schema=False)
def sso_login(token: str):
    """Deprecated entry point, kept for links already handed out.

    It used to consume the token and set a session cookie on a bare top-level
    navigation, so following a link was the whole login -- the single cheapest
    version of the attack above. It now only forwards to the confirmation page,
    moving the token into the fragment on the way so it also stops reaching
    access logs. Nothing is consumed here.
    """
    return RedirectResponse(url=f"/sso#{quote(token, safe='')}", status_code=302)


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    """Serve the built React app directly from FastAPI on the panel port."""
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    requested = (frontend_dist / full_path).resolve()
    try:
        requested.relative_to(frontend_dist.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if requested.is_file():
        return FileResponse(requested)
    if full_path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not found")
    index = frontend_dist / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"detail": "Frontend build not found", "path": str(index)}
