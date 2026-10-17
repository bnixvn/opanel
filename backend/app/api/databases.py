from pathlib import Path
from tempfile import NamedTemporaryFile
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from hmac import compare_digest
from typing import List
import ipaddress
import logging

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.access import ensure_owner_access, scope_owner
from app.core.permissions import Role, ensure_role, is_admin_role
from app.core.secrets import decrypt, encrypt
from app.models.entities import DatabaseAccount, User, Website
from app.schemas.schemas import DatabaseAdopt, DatabaseCreate, DatabaseCreatedOut, DatabaseOut, DatabaseOwnerUpdate, DatabasePasswordUpdate
from app.services import da_import, mariadb, panel_urls, site_users
from app.services.audit import log_action
from app.services.sso_tokens import consume_phpmyadmin_token, create_phpmyadmin_token

router = APIRouter(prefix="/databases", tags=["databases"])

logger = logging.getLogger("opanel.databases")


def _is_loopback_peer(request: Request) -> bool:
    """Return True if the immediate TCP peer is loopback.

    With ``--forwarded-allow-ips 127.0.0.1`` (see installer/install.sh) any
    X-Forwarded-For from a non-trusted peer is dropped, so request.client.host
    is the real connecting address.
    """
    if not request.client:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def get_accessible_database(database_id: int, db: Session, current_user: User) -> DatabaseAccount:
    item = db.query(DatabaseAccount).filter(DatabaseAccount.id == database_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Database not found")
    ensure_owner_access(db, current_user, item.owner_id)
    return item


@router.get("", response_model=List[DatabaseOut])
def list_databases(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = scope_owner(db.query(DatabaseAccount), DatabaseAccount.owner_id, db, current_user)
    return query.order_by(DatabaseAccount.id.desc()).all()


# How big each database the caller can see is: asked by the Databases page
# only, apart from the list, which every page load fetches.
@router.get("/sizes")
def list_database_sizes(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    items = scope_owner(db.query(DatabaseAccount), DatabaseAccount.owner_id, db, current_user).all()
    sizes = mariadb.database_sizes(item.db_name for item in items)
    return {str(item.id): sizes[item.db_name] for item in items if item.db_name in sizes}


# ---------------------------------------------------------------------------
# Databases MariaDB has and the panel does not
# ---------------------------------------------------------------------------
@router.get("/unmanaged")
def list_unmanaged_databases(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The databases nobody in the panel owns, with who can reach each one.

    Made with the mysql client, or left by an import. Each comes with its size
    and the accounts whose grants reach it, and whether each of those could be
    adopted along with it: a local account the panel does not already use,
    granted on exactly this name.
    """
    ensure_role(current_user.role, Role.admin)
    try:
        schemas, grants = mariadb.schemas(), mariadb.db_grants()
    except mariadb.MariaDBError as exc:
        raise HTTPException(status_code=502, detail=f"MariaDB error: {exc}") from exc
    rows = db.query(DatabaseAccount.db_name, DatabaseAccount.db_user).all()
    managed_names = {row.db_name for row in rows}
    managed_users = {row.db_user for row in rows}
    out = []
    for schema in schemas:
        name = schema["name"]
        if name in managed_names:
            continue
        valid = mariadb.valid_identifier(name)
        grantees = []
        for grant in grants:
            if not mariadb.grant_matches(name, grant["db"]):
                continue
            managed = grant["user"] in managed_users
            grantees.append({
                **grant,
                "managed": managed,
                "adoptable": (valid and not managed and grant["host"] == "localhost"
                              and mariadb.grant_is_literal(name, grant["db"])
                              and mariadb.valid_identifier(grant["user"])),
            })
        out.append({
            "db_name": name, "size_bytes": schema["size_bytes"], "tables": schema["tables"],
            "grantees": grantees, "adoptable": valid,
            "reason": None if valid else "The name has characters the panel does not use; rename it in MariaDB first",
        })
    return {"databases": out}


def _point_site_configs(website: Website, db_name: str, db_user: str, db_password: str) -> list[str]:
    """Give the website's apps that use this database its new account.

    Only a config that already names the database is changed - a second app
    in a subfolder has a database of its own.
    """
    if settings.command_dry_run or not website.linux_user:
        return []
    root = Path(website.root_path).resolve()
    public = site_users.document_root(root, website.document_root or site_users.PUBLIC_DIR)
    write = da_import._site_config_writer(str(root), website.linux_user)
    changed: list[Path] = []
    for directory in da_import._candidate_config_dirs(public):
        if da_import._config_targets_database(directory, db_name):
            changed += da_import._update_config_dir(directory, db_name, db_user, db_password, write)
    return [str(path.relative_to(root)).replace("\\", "/") if path.is_relative_to(root) else str(path)
            for path in changed]


@router.post("/adopt")
def adopt_database(payload: DatabaseAdopt, request: Request, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """Make a database MariaDB has, and the panel does not, an account's.

    It is reached through an account it already has (`db_user`: local only,
    not another panel database's, and reaching nothing else - adopting it
    hands the new owner all it can do) or a new one (`new_user`, by default
    the database's name). Either way it gets a password the panel knows,
    shown once. Other accounts' grants on exactly this database are taken
    away unless `revoke_others` is off; one that reaches it through a wider
    pattern is only named in `warnings`. On a website, the configs that use
    the database are given the new account.
    """
    ensure_role(current_user.role, Role.admin)
    db_name = payload.db_name
    if not mariadb.valid_identifier(db_name) or db_name in mariadb.SYSTEM_SCHEMAS:
        raise HTTPException(status_code=400, detail="This database cannot be adopted")
    if payload.db_user and payload.new_user:
        raise HTTPException(status_code=400, detail="Adopt an existing account or make a new one, not both")
    owner = db.query(User).filter(User.id == payload.owner_id, User.is_active.is_(True)).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found or inactive")
    website = None
    if payload.website_id is not None:
        website = db.query(Website).filter(Website.id == payload.website_id).first()
        if not website or website.owner_id != owner.id:
            raise HTTPException(status_code=400, detail="Choose one of the owner's websites")
    if db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first():
        raise HTTPException(status_code=409, detail="The panel already manages this database")
    if not payload.force and not is_admin_role(owner.role):
        limit = getattr(owner, "database_limit", 0) or 0
        owned = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == owner.id).count()
        if limit > 0 and owned >= limit:
            raise HTTPException(status_code=403, detail=f"Database limit reached ({owned}/{limit}).")

    existing = bool(payload.db_user)
    db_user = payload.db_user or payload.new_user or db_name
    if not mariadb.valid_identifier(db_user):
        raise HTTPException(status_code=400, detail=f"'{db_user}' cannot be used as the account")
    if db.query(DatabaseAccount).filter(DatabaseAccount.db_user == db_user).first():
        raise HTTPException(status_code=409, detail="That account belongs to another panel database")

    try:
        if not mariadb.schema_exists(db_name):
            raise HTTPException(status_code=404, detail="MariaDB has no database of that name")
        hosts = mariadb.account_hosts(db_user)
        if existing:
            if hosts != ["localhost"]:
                raise HTTPException(status_code=409, detail=(
                    f"MariaDB has no account '{db_user}'" if not hosts else
                    f"'{db_user}' exists as {', '.join(f'{db_user}@{h}' for h in hosts)}; only an account "
                    "on localhost alone can be adopted - make a new account instead"))
            beyond = mariadb.reaches_beyond(db_user, db_name)
            if beyond:
                raise HTTPException(status_code=409, detail=(
                    f"'{db_user}' also has privileges on {', '.join(beyond)}; adopting it would hand those "
                    "over too - make a new account instead"))
        elif hosts:
            raise HTTPException(status_code=409, detail=(
                f"MariaDB account '{db_user}' already exists. Choose another name, or adopt it."))
        db_password = mariadb.random_password()
        mariadb.adopt_account(db_name, db_user, db_password, existing=existing)
    except mariadb.MariaDBError as exc:
        raise HTTPException(status_code=502, detail=f"MariaDB error: {exc}") from exc
    except RuntimeError as exc:
        logger.exception("Adopting %s failed", db_name)
        raise HTTPException(status_code=500, detail=f"MariaDB error: {exc}") from exc

    item = DatabaseAccount(owner_id=owner.id, website_id=website.id if website else None,
                           db_name=db_name, db_user=db_user, db_password=encrypt(db_password))
    db.add(item)
    db.commit()
    db.refresh(item)

    revoked, warnings = [], []
    try:
        grants = mariadb.db_grants()
    except mariadb.MariaDBError as exc:
        grants = []
        warnings.append(f"Other accounts' access was not checked: {exc}")
    for grant in grants:
        if not mariadb.grant_matches(db_name, grant["db"]):
            continue
        if grant["user"] == db_user and grant["host"] == "localhost":
            continue
        who = f"{grant['user']}@{grant['host']}"
        if not mariadb.grant_is_literal(db_name, grant["db"]):
            warnings.append(f"{who} still reaches this database through its grant on `{grant['db']}`, "
                            "which covers other names too.")
        elif not payload.revoke_others:
            warnings.append(f"{who} can still reach this database.")
        else:
            try:
                mariadb.revoke_db_grant(grant["user"], grant["host"], grant["db"])
                revoked.append(who)
            except RuntimeError as exc:
                warnings.append(f"{who} kept its access: {exc}")

    configs_updated = []
    if website:
        try:
            configs_updated = _point_site_configs(website, db_name, db_user, db_password)
        except Exception as exc:  # the database is adopted either way
            logger.exception("Updating %s's configs for %s failed", website.domain, db_name)
            warnings.append(f"{website.domain}: its config could not be given the new password ({exc}).")

    log_action(db, current_user.id, "adopt_database", db_name,
               f"owner={owner.username} website={website.domain if website else '-'} user={db_user} "
               f"account={'existing' if existing else 'new'} revoked={','.join(revoked) or 'none'}",
               request=request)
    return {
        **DatabaseOut.model_validate(item).model_dump(),
        "db_password": db_password,
        "adopted_account": "existing" if existing else "new",
        "revoked": revoked,
        "warnings": warnings,
        "configs_updated": configs_updated,
    }


@router.post("", response_model=DatabaseCreatedOut)
def create_database(payload: DatabaseCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_name = payload.db_name
    db_user = payload.db_user or db_name
    db_password = payload.db_password or mariadb.random_password()

    # Whose database it is: the caller's, or an account the caller manages.
    owner = current_user
    if payload.owner_id is not None and payload.owner_id != current_user.id:
        ensure_owner_access(db, current_user, payload.owner_id)
        owner = db.query(User).filter(User.id == payload.owner_id, User.is_active.is_(True)).first()
        if not owner:
            raise HTTPException(status_code=404, detail="Owner not found or inactive")
    # The website it belongs to: one of the owner's, whose backups then carry it.
    website_id = None
    if payload.website_id is not None:
        site = db.query(Website).filter(Website.id == payload.website_id).first()
        if not site or site.owner_id != owner.id:
            raise HTTPException(status_code=400, detail="Choose one of the owner's websites")
        website_id = site.id

    # Count quota. Databases were the one tenant resource with no cap: websites
    # are metered against owner.website_limit and bytes against
    # storage_limit_mb, both with non-zero defaults, while this path had
    # nothing. Each create also forks two mysql clients and ends its DDL batch
    # with FLUSH PRIVILEGES -- a globally locking privilege reload whose cost
    # grows with the number of accounts already present -- so an uncapped path
    # made every later create slower for every tenant on the instance.
    # 0 means unlimited, as it does for storage_limit_mb.
    # The owner's package limit - which an administrator may choose to pass.
    forced = payload.force and is_admin_role(current_user.role)
    if not is_admin_role(owner.role) and not forced:
        limit = getattr(owner, "database_limit", 0) or 0
        if limit > 0:
            owned = db.query(DatabaseAccount).filter(
                DatabaseAccount.owner_id == owner.id
            ).count()
            if owned >= limit:
                raise HTTPException(
                    status_code=403,
                    detail=f"Database limit reached ({owned}/{limit}). Ask your administrator to raise it.",
                )

    if db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first():
        raise HTTPException(status_code=409, detail="Database name already exists")
    if db.query(DatabaseAccount).filter(DatabaseAccount.db_user == db_user).first():
        raise HTTPException(status_code=409, detail="Database user already exists")
    # The panel table only knows what the panel made. Ask MariaDB as well, or a
    # name it already holds would be silently taken over.
    in_use = mariadb.identifier_in_use(db_name, db_user)
    if in_use:
        raise HTTPException(status_code=409, detail=in_use)

    try:
        mariadb.create_database_credentials(db_name, db_user, db_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create MariaDB database/user")
        raise HTTPException(status_code=500, detail=f"MariaDB error: {exc}") from exc

    item = DatabaseAccount(
        owner_id=owner.id,
        website_id=website_id,
        db_name=db_name,
        db_user=db_user,
        db_password=encrypt(db_password),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return DatabaseCreatedOut(
        id=item.id,
        owner_id=item.owner_id,
        website_id=item.website_id,
        db_name=item.db_name,
        db_user=item.db_user,
        db_password=db_password,
    )


@router.delete("/{database_id}")
def delete_database_record(database_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    try:
        mariadb.drop_database(item.db_name, item.db_user)
    except Exception as exc:
        logger.exception("Failed to delete MariaDB database/user")
        raise HTTPException(status_code=500, detail=f"MariaDB error: {exc}") from exc
    try:
        db.delete(item)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Failed to delete database record")
        raise HTTPException(status_code=500, detail="Panel database error") from exc
    return {"ok": True}


@router.get("/{database_id}/download")
def download_database(database_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    temp_path = None
    try:
        temp_file = NamedTemporaryFile(prefix=f"{item.db_name}-", suffix=".sql", delete=False)
        temp_file.close()
        temp_path = Path(temp_file.name)
        mariadb.export_database(item.db_name, str(temp_path))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(
        temp_path,
        filename=f"{item.db_name}.sql",
        media_type="application/sql",
        background=BackgroundTask(lambda path: path.unlink(missing_ok=True), temp_path),
    )


@router.get("/phpmyadmin-sso/{token}")
def consume_phpmyadmin_sso(token: str, request: Request):
    """Consume a one-shot phpMyAdmin SSO token.

    Security model:
      * 256-bit token entropy (secrets.token_urlsafe(32)).
      * One-shot (file removed on read), TTL 60 seconds.
      * Requires a shared secret header (X-OPanel-Signon-Secret) that is
        generated at install time and injected into the phpMyAdmin signon
        script. This is more robust than IP-based loopback checking because
        it cannot be bypassed via X-Forwarded-For spoofing.
      * Falls back to loopback peer check when no secret is configured
        (backward compatibility for installs that predate this feature).
    """
    if settings.pma_signon_secret:
        provided = request.headers.get("x-opanel-signon-secret", "")
        if not provided or not compare_digest(provided, settings.pma_signon_secret):
            raise HTTPException(status_code=404, detail="Invalid or expired token")
    elif not _is_loopback_peer(request):
        peer = request.client.host if request.client else "unknown"
        logger.warning("phpmyadmin-sso non-loopback access attempt from %s", peer)
        raise HTTPException(status_code=404, detail="Invalid or expired token")
    data = consume_phpmyadmin_token(token)
    if not data:
        raise HTTPException(status_code=404, detail="Invalid or expired token")
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/{database_id}/phpmyadmin-sso")
def create_phpmyadmin_sso(database_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    try:
        db_password = decrypt(item.db_password)
    except RuntimeError:
        raise HTTPException(
            status_code=500,
            detail="Failed to access stored database password; please re-save the password in panel settings",
        )
    token = create_phpmyadmin_token(item.db_user, db_password, item.db_name)
    log_action(
        db,
        current_user.id,
        "phpmyadmin_sso",
        f"db={item.db_name}",
        request=request,
    )
    return {"url": f"{panel_urls.tools_base_url(request)}/phpmyadmin/opanel-signon.php?opanel_sso={token}"}


@router.post("/{database_id}/password")
def change_database_password(database_id: int, payload: DatabasePasswordUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    item = get_accessible_database(database_id, db, current_user)
    mariadb.change_database_password(item.db_user, payload.password)
    item.db_password = encrypt(payload.password)
    db.commit()
    return {"ok": True, "db_user": item.db_user}


@router.post("/{database_id}/owner", response_model=DatabaseOut)
def change_database_owner(database_id: int, payload: DatabaseOwnerUpdate, request: Request,
                          db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Hand a database to another panel account.

    Only the panel's record of who owns it changes: the database, its MySQL
    user and its grants are untouched, so whatever is using it keeps working on
    the same credentials.

    A database attached to a website is not moved on its own -- that is how the
    website and its data end up owned by different accounts, which is the state
    this endpoint exists to repair. Move the website instead and the database
    follows it.
    """
    ensure_role(current_user.role, Role.admin)
    item = db.query(DatabaseAccount).filter(DatabaseAccount.id == database_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Database not found")
    owner = db.query(User).filter(User.id == payload.owner_id, User.is_active.is_(True)).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found or inactive")

    if item.website_id:
        website = db.query(Website).filter(Website.id == item.website_id).first()
        if website and website.owner_id != owner.id:
            raise HTTPException(
                status_code=400,
                detail=(f"{item.db_name} belongs to {website.domain}. Reassign that website "
                        "instead and the database moves with it."),
            )
    # A database on no website may be put on one of its new owner's.
    if payload.website_id is not None and not item.website_id:
        target = db.query(Website).filter(Website.id == payload.website_id).first()
        if not target or target.owner_id != owner.id:
            raise HTTPException(status_code=400, detail="Choose one of the new owner's websites")
        item.website_id = target.id

    previous = item.owner_id
    item.owner_id = owner.id
    db.commit()
    db.refresh(item)
    log_action(db, current_user.id, "change_database_owner", item.db_name,
               f"{previous} -> {owner.id}", request=request)
    return item


# ---------------------------------------------------------------------------
# MariaDB auto-tuning
# ---------------------------------------------------------------------------
@router.get("/mariadb/tuning")
def get_mariadb_tuning(current_user: User = Depends(get_current_user)):
    """Return current OPanel MariaDB config + hardware recommendation."""
    ensure_role(current_user.role, Role.admin)
    current = mariadb.read_mariadb_tuning()
    recommendation = mariadb.recommend_mariadb_config()
    return {"current": current, "recommendation": recommendation}


@router.post("/mariadb/tuning")
def apply_mariadb_tuning(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Retune MariaDB through the helper (the only tuner); it restarts only
    when a setting changed."""
    ensure_role(current_user.role, Role.admin)
    result = mariadb.apply_mariadb_tuning()
    log_action(db, current_user.id, "mariadb_tuning_apply",
               f"pool={result['innodb_buffer_pool_size']} conn={result['max_connections']}")
    return result
