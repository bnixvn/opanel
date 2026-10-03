"""A reseller's share of the server, and what it has handed out of it.

The pool is set by the admin (users.pool_*, 0 = unlimited). Out of it come
the reseller's own account limits and every customer's: the sum of each
resource across those accounts may not exceed the pool. The per-account limits
are therefore still what websites, databases, mailboxes and disk are checked
against day to day; the pool only bounds what the reseller can hand out.

A limit that means "unlimited" when it is 0 (storage, databases, mailboxes)
cannot come out of a limited pool: it would be a blank cheque on it. Websites
are different: 0 websites means none.

An admin may let a reseller oversell (users.pool_oversell), as cPanel and
DirectAdmin do (operator, 2026-10-03). The limits it hands out are then not
added up at all; instead what its accounts actually hold - websites,
databases, mailboxes, disk - is checked against the pool whenever one of them
creates more (ensure_room, ensure_storage_room). Customers are counted the
same way either way.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import User

# (account limit, pool limit, 0 means unlimited for an account, label)
RESOURCES = (
    ("website_limit", "pool_website_limit", False, "websites"),
    ("storage_limit_mb", "pool_storage_limit_mb", True, "MB of storage"),
    ("database_limit", "pool_database_limit", True, "databases"),
    ("mailbox_limit", "pool_mailbox_limit", True, "mailboxes"),
)
POOL_FIELDS = ("pool_user_limit",) + tuple(pool for _, pool, _, _ in RESOURCES)
OVERSELL = "pool_oversell"
# Every pool setting, and what an account that is not a reseller holds.
SETTINGS = POOL_FIELDS + (OVERSELL,)
NO_POOL = {**{field: 0 for field in POOL_FIELDS}, OVERSELL: False}
LIMIT_FIELDS = tuple(field for field, _, _, _ in RESOURCES)


def customers(db: Session, reseller: User) -> list[User]:
    if reseller.id is None:
        # Not saved yet: no customers - and `reseller_id == None` would match
        # every account the admin owns.
        return []
    return db.query(User).filter(User.reseller_id == reseller.id).order_by(User.id).all()


def _limits(account: User, changes: Optional[dict]) -> dict:
    values = {field: int(getattr(account, field) or 0) for field in LIMIT_FIELDS}
    for field, value in (changes or {}).items():
        if field in values and value is not None:
            values[field] = int(value)
    return values


def check_pool(db: Session, reseller: User, *, pool: Optional[dict] = None,
               changes: Optional[dict[int, dict]] = None, new_account: Optional[dict] = None,
               leaving: Optional[set[int]] = None) -> None:
    """Raise ValueError if the reseller's accounts would not fit its pool.

    ``pool`` overrides the stored pool (an admin editing it), ``changes`` maps
    account id -> limits being set, ``new_account`` is the limits of a customer
    about to be created, ``leaving`` the ids of customers about to go.
    """
    pools = {field: int(getattr(reseller, field) or 0) for field in POOL_FIELDS}
    for field, value in (pool or {}).items():
        if field in pools and value is not None:
            pools[field] = int(value)
    oversell = bool(getattr(reseller, OVERSELL, False))
    if (pool or {}).get(OVERSELL) is not None:
        oversell = bool(pool[OVERSELL])
    changes = changes or {}
    leaving = leaving or set()

    accounts = [_limits(reseller, changes.get(reseller.id))]
    members = [c for c in customers(db, reseller) if c.id not in leaving]
    accounts += [_limits(c, changes.get(c.id)) for c in members]
    customer_count = len(members)
    if new_account is not None:
        accounts.append({field: int(new_account.get(field) or 0) for field in LIMIT_FIELDS})
        customer_count += 1

    if pools["pool_user_limit"] and customer_count > pools["pool_user_limit"]:
        raise ValueError(
            f"This reseller may have {pools['pool_user_limit']} customers; this would make {customer_count}."
        )
    if oversell:
        # What the accounts hold is checked when they create it, not here.
        return
    for field, pool_field, zero_unlimited, label in RESOURCES:
        limit = pools[pool_field]
        if not limit:
            continue
        if zero_unlimited and any(account[field] == 0 for account in accounts):
            raise ValueError(
                f"The reseller's {label} are limited to {limit}, so every account under it needs a "
                f"{label} limit; 0 (unlimited) is not available."
            )
        total = sum(account[field] for account in accounts)
        if total > limit:
            raise ValueError(
                f"The reseller's accounts would hold {total} {label}; its share is {limit}."
            )


def usage(db: Session, reseller: User) -> dict:
    """The pool, what has been handed out of it and what is in use, for the panel."""
    members = customers(db, reseller)
    accounts = [reseller] + members
    out = {"customers": len(members), "pool_user_limit": int(reseller.pool_user_limit or 0),
           OVERSELL: bool(getattr(reseller, OVERSELL, False))}
    for field, pool_field, zero_unlimited, _label in RESOURCES:
        values = [int(getattr(account, field) or 0) for account in accounts]
        out[pool_field] = int(getattr(reseller, pool_field) or 0)
        out[f"allocated_{field}"] = None if zero_unlimited and 0 in values else sum(values)
    ids = [account.id for account in accounts]
    for resource in ("website", "database", "mailbox"):
        out[f"used_{resource}_limit"] = _held(db, resource, ids)
    out["used_storage_limit_mb"] = _storage_used_bytes(db, accounts) // (1024 * 1024)
    return out


# --- overselling: what the accounts actually hold --------------------------------------

_POOL_OF = {"website": ("pool_website_limit", "websites"),
            "database": ("pool_database_limit", "databases"),
            "mailbox": ("pool_mailbox_limit", "mailboxes")}


def reseller_of(db: Session, account: User) -> Optional[User]:
    """The reseller whose share this account's resources come out of."""
    from app.core.permissions import is_reseller_role

    if is_reseller_role(account.role):
        return account
    if not account.reseller_id:
        return None
    owner = db.query(User).filter(User.id == account.reseller_id).first()
    return owner if owner is not None and is_reseller_role(owner.role) else None


def _held(db: Session, resource: str, owner_ids: list[int]) -> int:
    from sqlalchemy import func

    from app.models.entities import DatabaseAccount, MailDomain, Mailbox, Website

    if resource == "website":
        return db.query(func.count(Website.id)).filter(Website.owner_id.in_(owner_ids)).scalar() or 0
    if resource == "database":
        return db.query(func.count(DatabaseAccount.id)).filter(DatabaseAccount.owner_id.in_(owner_ids)).scalar() or 0
    return (db.query(func.count(Mailbox.id)).join(MailDomain)
            .filter(MailDomain.owner_id.in_(owner_ids)).scalar() or 0)


def ensure_room(db: Session, account: User, resource: str, adding: int = 1) -> None:
    """Refuse a new website, database or mailbox for an account under an
    overselling reseller whose accounts already hold its share."""
    reseller = reseller_of(db, account)
    if reseller is None or not getattr(reseller, OVERSELL, False):
        return
    pool_field, label = _POOL_OF[resource]
    limit = int(getattr(reseller, pool_field) or 0)
    if not limit:
        return
    held = _held(db, resource, [reseller.id] + [c.id for c in customers(db, reseller)])
    if held + adding > limit:
        raise ValueError(f"The reseller's share of {label} is used up ({held}/{limit}).")


def _storage_used_bytes(db: Session, accounts: list[User], fresh: Optional[User] = None) -> int:
    from app.services import storage_quota

    return sum(storage_quota.user_storage_used_bytes(db, account, use_cache=fresh is None or account.id != fresh.id)
               for account in accounts)


def ensure_storage_room(db: Session, account: User, *, incoming_bytes: int = 0, replaced_bytes: int = 0) -> None:
    """The same for disk. The account writing is measured afresh; the others
    come from the usage cache, which is minutes old at most."""
    reseller = reseller_of(db, account)
    if reseller is None or not getattr(reseller, OVERSELL, False):
        return
    limit_mb = int(reseller.pool_storage_limit_mb or 0)
    if not limit_mb or max(0, incoming_bytes) <= max(0, replaced_bytes):
        return
    accounts = [reseller] + customers(db, reseller)
    used = _storage_used_bytes(db, accounts, fresh=account)
    projected = max(0, used - max(0, replaced_bytes)) + max(0, incoming_bytes)
    if projected > limit_mb * 1024 * 1024:
        raise ValueError(
            f"The reseller's share of disk is used up: {projected // (1024 * 1024)} MB used/projected, "
            f"share {limit_mb} MB."
        )
