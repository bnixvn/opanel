"""open_basedir for a site's PHP: off unless the site, or its package, asks.

open_basedir fences a site's PHP into its own tree, and PHP turns its realpath
cache off while it is set: on .41 that cost about a fifth of every uncached
WordPress page (2026-10-23). Tenants do not depend on it to stay apart - each
site's PHP runs as its own user and /home/<user> is 0750 - so it is a choice,
and a host's: off by default, on for every site of a package that has it on,
and settable per site by whoever manages the account (operator, 2026-10-22).

The package is the account's (users.plan_id). Turning it on or off for a
package moves every site of every account on that package with it; putting
an account on a package moves that account's sites to the package's value.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import HostingPlan, User, Website


def plan_of(db: Session, user: User | None) -> HostingPlan | None:
    plan_id = getattr(user, "plan_id", None) if user is not None else None
    if not plan_id:
        return None
    return db.query(HostingPlan).filter(HostingPlan.id == plan_id).first()


def default_for(db: Session, owner: User | None) -> bool:
    """What a new site of this owner starts with: its package's value, else off."""
    plan = plan_of(db, owner)
    return bool(plan and plan.open_basedir)


def apply(db: Session, websites: list[Website], on: bool) -> list[str]:
    """Set the flag on these sites and rewrite each one whose value moved.

    Returns the domains whose vhost could not be rewritten; their flag is left
    as it was, so the database never claims what the vhost does not do.
    """
    from app.api.websites import _rewrite_website_vhost

    failed: list[str] = []
    for website in websites:
        if bool(website.open_basedir) == bool(on):
            continue
        try:
            _rewrite_website_vhost(website, open_basedir=bool(on))
        except (RuntimeError, ValueError, OSError):
            failed.append(website.domain)
            continue
        website.open_basedir = bool(on)
    db.commit()
    return failed


def sync_user(db: Session, user: User) -> list[str]:
    """Bring an account's sites to its package's value (after a package change)."""
    plan = plan_of(db, user)
    if plan is None:
        return []
    sites = db.query(Website).filter(Website.owner_id == user.id).all()
    return apply(db, sites, bool(plan.open_basedir))


def sync_plan(db: Session, plan: HostingPlan) -> list[str]:
    """Every site of every account on this package, to the package's value."""
    owner_ids = [row[0] for row in db.query(User.id).filter(User.plan_id == plan.id).all()]
    if not owner_ids:
        return []
    sites = db.query(Website).filter(Website.owner_id.in_(owner_ids)).all()
    return apply(db, sites, bool(plan.open_basedir))
