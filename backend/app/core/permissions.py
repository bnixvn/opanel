from enum import StrEnum

from fastapi import HTTPException, status


class Role(StrEnum):
    admin = "admin"
    # Sells hosting from its own share of the server: manages its customers
    # (users.reseller_id) and its own packages, and hosts sites of its own.
    # Never anything server-wide. See app/core/access.py.
    reseller = "reseller"
    end_user = "end_user"


LEGACY_ROLE_ALIASES = {
    "super_admin": Role.admin,
    "user": Role.end_user,
    "readonly": Role.end_user,
}


# ensure_role(x, Role.admin) stays admin-only; ensure_role(x, Role.end_user)
# lets every signed-in role through.
ROLE_LEVEL = {
    Role.end_user: 1,
    Role.reseller: 2,
    Role.admin: 3,
}


def normalize_role(current_role: str) -> Role:
    if current_role in LEGACY_ROLE_ALIASES:
        return LEGACY_ROLE_ALIASES[current_role]
    try:
        return Role(current_role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid role") from exc


def is_admin_role(current_role: str) -> bool:
    return normalize_role(current_role) == Role.admin


def is_reseller_role(current_role: str) -> bool:
    return normalize_role(current_role) == Role.reseller


def ensure_role(current_role: str, minimum: Role) -> None:
    role = normalize_role(current_role)
    if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL[minimum]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
