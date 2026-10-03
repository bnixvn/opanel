"""A reseller's share of the server, and what it has handed out of it.

The pool is set by the admin (users.pool_*, 0 = unlimited). Out of it come
the reseller's own account limits and every customer's: the sum of each
resource across those accounts may not exceed the pool. The per-account limits
are therefore still what websites, databases, mailboxes and disk are checked
against day to day; the pool only bounds what the reseller can hand out.

A limit that means "unlimited" when it is 0 (storage, databases, mailboxes)
cannot come out of a limited pool: it would be a blank cheque on it. Websites
are different: 0 websites means none.
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
    """The pool and what has been handed out of it, for the panel."""
    members = customers(db, reseller)
    accounts = [reseller] + members
    out = {"customers": len(members), "pool_user_limit": int(reseller.pool_user_limit or 0)}
    for field, pool_field, zero_unlimited, _label in RESOURCES:
        values = [int(getattr(account, field) or 0) for account in accounts]
        out[pool_field] = int(getattr(reseller, pool_field) or 0)
        out[f"allocated_{field}"] = None if zero_unlimited and 0 in values else sum(values)
    return out
