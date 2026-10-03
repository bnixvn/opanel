"""reseller role: users.reseller_id, a reseller's pool, and resellers' own packages

A reseller sells hosting from its own share of the server. Its customers point
at it through users.reseller_id; the pool_* columns are its share (0 =
unlimited); hosting_plans.owner_id marks a package as one reseller's own. The
plans also gain the mailbox limit the users already had.

Revision ID: 0037_reseller
Revises: 0036_dns_managed
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0037_reseller"
down_revision: Union[str, None] = "0036_dns_managed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POOL_COLUMNS = (
    "pool_user_limit",
    "pool_website_limit",
    "pool_storage_limit_mb",
    "pool_database_limit",
    "pool_mailbox_limit",
)


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    users = _columns("users")
    with op.batch_alter_table("users") as batch:
        if "reseller_id" not in users:
            batch.add_column(sa.Column("reseller_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_users_reseller_id", "users", ["reseller_id"], ["id"])
        for name in POOL_COLUMNS:
            if name not in users:
                batch.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    if "ix_users_reseller_id" not in _indexes("users"):
        op.create_index("ix_users_reseller_id", "users", ["reseller_id"])

    plans = _columns("hosting_plans")
    with op.batch_alter_table("hosting_plans") as batch:
        if "mailbox_limit" not in plans:
            batch.add_column(sa.Column("mailbox_limit", sa.Integer(), nullable=False, server_default="10"))
        if "owner_id" not in plans:
            batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_hosting_plans_owner_id", "users", ["owner_id"], ["id"])
    if "ix_hosting_plans_owner_id" not in _indexes("hosting_plans"):
        op.create_index("ix_hosting_plans_owner_id", "hosting_plans", ["owner_id"])


def downgrade() -> None:
    if "ix_hosting_plans_owner_id" in _indexes("hosting_plans"):
        op.drop_index("ix_hosting_plans_owner_id", table_name="hosting_plans")
    plans = _columns("hosting_plans")
    with op.batch_alter_table("hosting_plans") as batch:
        if "owner_id" in plans:
            batch.drop_constraint("fk_hosting_plans_owner_id", type_="foreignkey")
            batch.drop_column("owner_id")
        if "mailbox_limit" in plans:
            batch.drop_column("mailbox_limit")
    if "ix_users_reseller_id" in _indexes("users"):
        op.drop_index("ix_users_reseller_id", table_name="users")
    users = _columns("users")
    with op.batch_alter_table("users") as batch:
        for name in POOL_COLUMNS:
            if name in users:
                batch.drop_column(name)
        if "reseller_id" in users:
            batch.drop_constraint("fk_users_reseller_id", type_="foreignkey")
            batch.drop_column("reseller_id")
