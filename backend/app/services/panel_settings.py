import json
import os
import re
import time
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.services.shell import shell


SETTINGS_DIR = Path(os.environ.get("opanel_DATA_DIR", "/var/lib/opanel"))
SETTINGS_FILE = SETTINGS_DIR / "panel-settings.json"
ASSETS_DIR = SETTINGS_DIR / "assets"
MAX_ASSET_SIZE = 1024 * 1024
ALLOWED_ASSET_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "ico": "image/x-icon",
}
DOMAIN_RE = re.compile(r"^(?!-)([a-z0-9-]{1,63}\.)+[a-z]{2,}$")
IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def _read_raw() -> dict:
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _write_raw(data: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(SETTINGS_DIR), delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=True, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(SETTINGS_FILE)


def _asset_url(filename: str | None) -> str:
    if not filename:
        return ""
    path = ASSETS_DIR / filename
    if not path.exists():
        return ""
    stat = path.stat()
    version = f"{stat.st_mtime_ns}-{stat.st_size}"
    return f"/brand-assets/{filename}?v={version}"


def _is_ipv4(host: str) -> bool:
    if not IPV4_RE.fullmatch(host):
        return False
    return all(0 <= int(part) <= 255 for part in host.split("."))


def is_domain(host: str) -> bool:
    return bool(DOMAIN_RE.fullmatch((host or "").lower()))


def default_ssl_email(host: str) -> str:
    return f"admin@{host.lower()}" if is_domain(host) else ""


def normalize_panel_hostname(value: str) -> str:
    host = (value or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Panel hostname is required")
    if "://" in host or "/" in host or ":" in host:
        raise ValueError("Panel hostname must not include a scheme, port, or path")
    if not is_domain(host) and not _is_ipv4(host) and host != "localhost":
        raise ValueError("Panel hostname must be a domain name or IPv4 address")
    return host


def normalize_panel_port(value: int | str | None) -> int:
    try:
        port = int(value or settings.panel_port or 2222)
    except (TypeError, ValueError) as exc:
        raise ValueError("Panel port is invalid") from exc
    if port < 1 or port > 65535:
        raise ValueError("Panel port is out of range")
    return port


def normalize_panel_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("Panel URL is required")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Panel URL must start with http:// or https://")
    host = normalize_panel_hostname(host)
    port = normalize_panel_port(parsed.port or settings.panel_port or 2222)
    return f"{scheme}://{host}:{port}"


def panel_url_from_parts(hostname: str, port: int | str | None, scheme: str | None = None) -> str:
    safe_scheme = (scheme or "http").lower()
    if safe_scheme not in {"http", "https"}:
        raise ValueError("Panel URL scheme must be http or https")
    host = normalize_panel_hostname(hostname)
    safe_port = normalize_panel_port(port)
    return f"{safe_scheme}://{host}:{safe_port}"


def parse_panel_url(value: str) -> tuple[str, str, int]:
    normalized = normalize_panel_url(value)
    parsed = urlparse(normalized)
    return parsed.scheme, parsed.hostname or "", parsed.port or 2222


def has_panel_certificate() -> bool:
    cert_pairs = [
        (settings.panel_ssl_cert, settings.panel_ssl_key),
        ("/etc/opanel/panel-fullchain.pem", "/etc/opanel/panel-privkey.pem"),
    ]
    return any(bool(cert) and bool(key) and Path(cert).exists() and Path(key).exists() for cert, key in cert_pairs)


def current_settings() -> dict:
    data = _read_raw()
    app_name = (data.get("app_name") or settings.app_name or "OPanel").strip() or "OPanel"
    panel_url = data.get("panel_url") or settings.panel_url or ""
    panel_hostname = ""
    panel_port = settings.panel_port or 2222
    if panel_url:
        try:
            _scheme, panel_hostname, panel_port = parse_panel_url(panel_url)
        except ValueError:
            panel_hostname = ""
            panel_port = settings.panel_port or 2222
    ssl_enabled = panel_url.startswith("https://") and has_panel_certificate()
    from app.services import malware_scan

    mw = malware_scan.refresh_status()
    return {
        "app_name": app_name,
        "panel_url": panel_url,
        "panel_hostname": panel_hostname,
        "panel_port": panel_port,
        "logo_url": _asset_url(data.get("logo_filename")),
        "favicon_url": _asset_url(data.get("favicon_filename")) or "/favicon.png",
        "ssl_enabled": ssl_enabled,
        "malware_scan_enabled": mw["enabled"],
        "malware_scan_installed": mw["installed"],
        "malware_scan_active": mw["active"],
        "malware_scan_detail": mw["detail"],
    }


def update_settings(
    app_name: str | None = None,
    panel_hostname: str | None = None,
    panel_port: int | None = None,
    panel_url: str | None = None,
) -> dict:
    del panel_port  # The panel port is install-time only; settings can change hostname/branding.
    data = _read_raw()
    if app_name is not None:
        value = app_name.strip()
        if not 2 <= len(value) <= 80:
            raise ValueError("Panel name must be 2-80 characters")
        data["app_name"] = value
    if (panel_hostname is not None and panel_hostname.strip()) or (panel_url is not None and panel_url.strip()):
        existing_url = data.get("panel_url") or settings.panel_url or ""
        existing_normalized = normalize_panel_url(existing_url) if existing_url else ""
        existing_scheme, existing_host, existing_port = parse_panel_url(existing_normalized) if existing_normalized else ("http", "", settings.panel_port or 2222)
        if panel_hostname is not None and panel_hostname.strip():
            normalized = panel_url_from_parts(panel_hostname, existing_port, existing_scheme)
        elif panel_url is not None and panel_url.strip():
            requested_scheme, requested_host, _requested_port = parse_panel_url(panel_url)
            normalized = panel_url_from_parts(requested_host, existing_port, requested_scheme)
        else:
            normalized = existing_normalized
        if not normalized:
            raise ValueError("Panel hostname is required")
        scheme, host, port = parse_panel_url(normalized)
        if scheme == "https" and not has_panel_certificate():
            raise ValueError("Use Install SSL before saving an HTTPS panel URL")
        if normalized != existing_normalized:
            result = shell.privileged(
                "panel-url-set",
                helper_args=[scheme, host, str(port)],
                check=False,
                fallback=["bash", "-lc", "true"],
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Could not update panel URL").strip())
        data["panel_url"] = normalized
    _write_raw(data)
    return current_settings()


def detect_asset_type(content: bytes, filename: str) -> tuple[str, str]:
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp", "image/webp"
    if content.startswith(b"\x00\x00\x01\x00"):
        return "ico", "image/x-icon"
    if suffix in ALLOWED_ASSET_TYPES:
        raise ValueError("Uploaded file content does not match its image type")
    raise ValueError("Only PNG, JPG, WEBP, and ICO images are supported")


async def save_asset(kind: str, upload: UploadFile) -> dict:
    if kind not in {"logo", "favicon"}:
        raise ValueError("Invalid asset kind")
    content = await upload.read(MAX_ASSET_SIZE + 1)
    if len(content) > MAX_ASSET_SIZE:
        raise ValueError("Image must be 1 MB or smaller")
    ext, _media_type = detect_asset_type(content, upload.filename or "")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    data = _read_raw()
    previous = data.get(f"{kind}_filename")
    if previous:
        try:
            (ASSETS_DIR / previous).unlink()
        except OSError:
            pass
    filename = f"{kind}.{ext}"
    (ASSETS_DIR / filename).write_bytes(content)
    data[f"{kind}_filename"] = filename
    _write_raw(data)
    return current_settings()


def asset_path(filename: str) -> tuple[Path, str]:
    if not re.fullmatch(r"(?:logo|favicon)\.(?:png|jpg|jpeg|webp|ico)", filename or ""):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    path = ASSETS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    media_type = ALLOWED_ASSET_TYPES.get(path.suffix.lower().lstrip("."), "application/octet-stream")
    return path, media_type


def install_panel_ssl(email: str | None = None, panel_hostname: str | None = None, panel_port: int | None = None, panel_url: str | None = None) -> dict:
    if panel_hostname:
        normalized = panel_url_from_parts(panel_hostname, panel_port, "http")
    elif panel_url:
        normalized = normalize_panel_url(panel_url)
    else:
        raise ValueError("Panel hostname is required")
    _scheme, host, port = parse_panel_url(normalized)
    if not is_domain(host):
        raise ValueError("Panel SSL requires a domain name, not an IP address")
    certbot_email = (email or settings.ssl_email or default_ssl_email(host)).strip()
    helper_args = [host, str(port)]
    if certbot_email:
        helper_args.append(certbot_email)
    result = shell.privileged(
        "panel-ssl-install",
        helper_args=helper_args,
        check=False,
        fallback=["bash", "-lc", "true"],
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Could not install panel SSL").strip())
    data = _read_raw()
    data["panel_url"] = f"https://{host}:{port}"
    _write_raw(data)
    current = current_settings()
    current["message"] = result.stdout.strip() or f"Panel SSL enabled for {host}"
    return current


# --------------------------------------------------------------------------
# Optional ClamAV malware scanning
# --------------------------------------------------------------------------
import threading

from app.services import malware_scan as _malware_scan

MALWARE_JOBS: dict[str, dict] = {}
MALWARE_JOB_THREADS: dict[str, threading.Thread] = {}
MALWARE_JOBS_LOCK = threading.RLock()
MALWARE_JOBS_DIR = SETTINGS_DIR / "malware-scan-jobs"
MAX_MALWARE_LOG_LINES = 1000


def _persist_malware_enabled(enabled: bool) -> None:
    """Write the malware_scan_enabled flag to the panel settings file so it
    survives restarts. The flag is the source of truth that the API reads via
    ``settings.malware_scan_enabled`` (overridden here from persisted state)."""
    data = _read_raw()
    data["malware_scan_enabled"] = bool(enabled)
    _write_raw(data)


def malware_scan_status() -> dict:
    return _malware_scan.refresh_status()


def set_malware_scan(enabled: bool) -> dict:
    """Toggle malware scanning on/off.

    If enabling and ClamAV is not yet installed, the install runs in a
    background thread so the API call returns immediately. If disabling, the
    flag is simply turned off (clamd is left installed but idle).
    """
    enabled = bool(enabled)
    if enabled and not _malware_scan.clamav_installed():
        # Kick off install + enable in the background; mark flag intent now.
        _persist_malware_enabled(True)
        threading.Thread(target=_install_and_enable_flow, daemon=True).start()
        current = current_settings()
        current["message"] = (
            "ClamAV is being installed in the background. Scanning will activate "
            "automatically once clamav-daemon is running."
        )
        return current
    _persist_malware_enabled(enabled)
    current = current_settings()
    if enabled:
        current["message"] = "Malware scanning enabled"
    else:
        stopped = _malware_scan.stop_clamd()
        current = current_settings()
        current["message"] = (
            "Malware scanning disabled and clamav-daemon stopped"
            if stopped
            else "Malware scanning disabled, but clamav-daemon could not be stopped"
        )
    return current


def _install_and_enable_flow() -> None:
    """Background worker: install ClamAV, then leave the persisted flag on.

    The flag was already persisted as True by set_malware_scan(); this just
    performs the heavy install. Failures are captured in the status file.
    """
    try:
        _malware_scan.install_clamav()
    except Exception as exc:  # noqa: BLE001 - record failure, do not crash thread
        _malware_scan._write_status(
            {
                "installed": _malware_scan.clamav_installed(),
                "clamd_running": False,
                "enabled": True,
                "active": False,
                "socket": _malware_scan._socket_path(),
                "detail": f"ClamAV install failed: {exc}",
            }
        )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _public_malware_job(job: dict) -> dict:
    return dict(job)


def _finalize_stale_malware_job(job: dict) -> dict:
    if job.get("status") not in {"queued", "running"}:
        return job
    with MALWARE_JOBS_LOCK:
        thread = MALWARE_JOB_THREADS.get(job.get("job_id"))
        if thread and thread.is_alive():
            return job
        MALWARE_JOB_THREADS.pop(job.get("job_id"), None)
    job = dict(job)
    job.update(
        status="interrupted",
        message="Scan interrupted. Start a new scan to continue.",
        error="Scan worker is no longer running.",
        finished_at=job.get("finished_at") or _now_iso(),
        updated_at=_now_iso(),
    )
    _write_malware_job(job)
    with MALWARE_JOBS_LOCK:
        MALWARE_JOBS[job["job_id"]] = job
    return job


def _write_malware_job(job: dict) -> None:
    try:
        MALWARE_JOBS_DIR.mkdir(parents=True, exist_ok=True)
        path = MALWARE_JOBS_DIR / f"{job['job_id']}.json"
        path.write_text(json.dumps(_public_malware_job(job), indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _remember_malware_job(job: dict) -> dict:
    with MALWARE_JOBS_LOCK:
        MALWARE_JOBS[job["job_id"]] = job
    _write_malware_job(job)
    return _public_malware_job(job)


def _update_malware_job(job_id: str, **updates) -> None:
    with MALWARE_JOBS_LOCK:
        job = MALWARE_JOBS.get(job_id)
        if not job:
            return
        updates.setdefault("updated_at", _now_iso())
        job.update(updates)
        if len(job.get("log", [])) > MAX_MALWARE_LOG_LINES:
            job["log"] = job["log"][-MAX_MALWARE_LOG_LINES:]
        snapshot = dict(job)
    _write_malware_job(snapshot)


def _append_malware_log(job_id: str, line: str) -> None:
    with MALWARE_JOBS_LOCK:
        job = MALWARE_JOBS.get(job_id)
        if not job:
            return
        job.setdefault("log", []).append(f"{_now_iso()} {line}")
        job["updated_at"] = _now_iso()
        if len(job["log"]) > MAX_MALWARE_LOG_LINES:
            job["log"] = job["log"][-MAX_MALWARE_LOG_LINES:]
        snapshot = dict(job)
    _write_malware_job(snapshot)


def get_malware_scan_job(job_id: str) -> dict:
    with MALWARE_JOBS_LOCK:
        job = MALWARE_JOBS.get(job_id)
        if job:
            return _public_malware_job(_finalize_stale_malware_job(job))
    path = MALWARE_JOBS_DIR / f"{job_id}.json"
    try:
        if path.exists():
            return _public_malware_job(_finalize_stale_malware_job(json.loads(path.read_text(encoding="utf-8"))))
    except (OSError, json.JSONDecodeError):
        pass
    raise ValueError("Scan job not found")


def get_latest_malware_scan_job() -> dict:
    with MALWARE_JOBS_LOCK:
        jobs = list(MALWARE_JOBS.values())
    try:
        if MALWARE_JOBS_DIR.exists():
            for path in MALWARE_JOBS_DIR.glob("*.json"):
                try:
                    jobs.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
    except OSError:
        pass
    unique: dict[str, dict] = {}
    for job in jobs:
        job_id = job.get("job_id")
        if not job_id:
            continue
        current = unique.get(job_id)
        if not current or (job.get("updated_at") or job.get("started_at") or job.get("created_at") or "") >= (
            current.get("updated_at") or current.get("started_at") or current.get("created_at") or ""
        ):
            unique[job_id] = job
    if not unique:
        raise ValueError("Scan job not found")
    running = [job for job in unique.values() if job.get("status") in {"queued", "running"}]
    candidates = running or list(unique.values())
    return _public_malware_job(
        _finalize_stale_malware_job(max(candidates, key=lambda job: job.get("updated_at") or job.get("started_at") or job.get("created_at") or ""))
    )


def list_malware_scan_jobs(limit: int = 50) -> list[dict]:
    jobs = []
    with MALWARE_JOBS_LOCK:
        jobs.extend(MALWARE_JOBS.values())
    try:
        if MALWARE_JOBS_DIR.exists():
            for path in MALWARE_JOBS_DIR.glob("*.json"):
                try:
                    jobs.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
    except OSError:
        pass
    unique: dict[str, dict] = {}
    for job in jobs:
        job_id = job.get("job_id")
        if not job_id:
            continue
        current = unique.get(job_id)
        stamp = job.get("updated_at") or job.get("started_at") or job.get("created_at") or ""
        current_stamp = current.get("updated_at") or current.get("started_at") or current.get("created_at") or "" if current else ""
        if not current or stamp >= current_stamp:
            unique[job_id] = job
    sorted_jobs = sorted(
        (_finalize_stale_malware_job(job) for job in unique.values()),
        key=lambda job: job.get("updated_at") or job.get("started_at") or job.get("created_at") or "",
        reverse=True,
    )
    return [_public_malware_job(job) for job in sorted_jobs[: max(1, min(limit, 200))]]


def _select_scan_websites(website_id: int | None, db) -> list:
    from app.models.entities import Website

    if website_id is None:
        return db.query(Website).order_by(Website.domain.asc()).all()
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise ValueError("Website not found")
    return [website]


def start_scan_job(website_id: int | None, db) -> dict:
    """Start a background malware scan for one website or all websites."""
    websites = _select_scan_websites(website_id, db)
    if not websites:
        raise ValueError("No websites found")
    targets = []
    for website in websites:
        root_path = website.root_path
        if not root_path or not Path(root_path).is_dir():
            raise ValueError(f"Website root not found: {root_path}")
        targets.append({"id": website.id, "domain": website.domain, "root_path": root_path})

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "status": "queued",
        "scope": "all" if website_id is None else "website",
        "website_id": website_id,
        "domains": [target["domain"] for target in targets],
        "message": "Queued",
        "progress_percent": 0,
        "total_files": 0,
        "scanned": 0,
        "infected": 0,
        "errors": 0,
        "skipped": 0,
        "threats": [],
        "log": [],
        "created_at": _now_iso(),
        "started_at": "",
        "finished_at": "",
        "updated_at": _now_iso(),
    }
    _remember_malware_job(job)
    thread = threading.Thread(target=_run_scan_job, args=(job_id, targets), daemon=True)
    with MALWARE_JOBS_LOCK:
        MALWARE_JOB_THREADS[job_id] = thread
    thread.start()
    return _public_malware_job(job)


def _run_scan_job(job_id: str, targets: list[dict]) -> None:
    _update_malware_job(job_id, status="running", started_at=_now_iso(), message="Preparing scan")
    try:
        if not _malware_scan.clamd_running():
            raise RuntimeError("ClamAV daemon is not running. Enable malware scanning first.")
        target_files: list[tuple[dict, str]] = []
        skipped = 0
        for target in targets:
            files, skipped_lines = _malware_scan.collect_regular_files(target["root_path"])
            skipped += len(skipped_lines)
            _append_malware_log(job_id, f"Prepared {target['domain']}: {len(files)} files")
            for line in skipped_lines:
                _append_malware_log(job_id, line)
            target_files.extend((target, file_path) for file_path in files)
        total = len(target_files)
        _update_malware_job(job_id, total_files=total, skipped=skipped, message=f"Scanning 0/{total} files")

        threats = []
        errors = 0
        scanned = 0
        for target, file_path in target_files:
            result = _malware_scan.scan_file_with_clamdscan(file_path)
            scanned += 1
            status = result["status"]
            if status == "infected":
                threat = {"path": file_path, "signature": result["signature"], "domain": target["domain"]}
                threats.append(threat)
                _append_malware_log(job_id, f"INFECTED {target['domain']} {file_path}: {result['signature']}")
            elif status == "error":
                errors += 1
                _append_malware_log(job_id, f"ERROR {target['domain']} {file_path}: {result['detail']}")
            elif status == "skipped":
                skipped += 1
                _append_malware_log(job_id, f"SKIP {target['domain']} {file_path}: {result['detail']}")

            percent = int((scanned / total) * 100) if total else 100
            _update_malware_job(
                job_id,
                scanned=scanned,
                infected=len(threats),
                errors=errors,
                skipped=skipped,
                threats=threats,
                progress_percent=percent,
                message=f"Scanning {scanned}/{total} files",
            )

        status = "infected" if threats else "done"
        _update_malware_job(
            job_id,
            status=status,
            progress_percent=100,
            scanned=scanned,
            infected=len(threats),
            errors=errors,
            skipped=skipped,
            threats=threats,
            message=f"Scan complete: {scanned} files, {len(threats)} threats, {errors} errors",
            finished_at=_now_iso(),
        )
        _append_malware_log(job_id, "Scan finished")
    except Exception as exc:  # noqa: BLE001 - scan jobs should report errors, not crash the API
        _update_malware_job(
            job_id,
            status="error",
            message="Scan failed",
            error=str(exc),
            finished_at=_now_iso(),
        )
        _append_malware_log(job_id, f"ERROR scan failed: {exc}")
    finally:
        with MALWARE_JOBS_LOCK:
            MALWARE_JOB_THREADS.pop(job_id, None)


def run_scan(website_id: int, db) -> dict:
    """Trigger an on-demand malware scan of a website's root directory."""
    from app.models.entities import Website

    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise ValueError("Website not found")
    root_path = website.root_path
    if not root_path or not Path(root_path).is_dir():
        raise ValueError(f"Website root not found: {root_path}")
    return _malware_scan.scan_directory(root_path)
