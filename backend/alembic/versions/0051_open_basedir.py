"""open_basedir per site and per package; an account's package

open_basedir was written into every site's vhost. It turns PHP's realpath
cache off, about a fifth of every uncached WordPress page, so it is now off
unless a site or its package asks (operator, 2026-10-22). Accounts get the
package they were given (users.plan_id), so a package that turns it on can
take its sites with it. WHMCS accounts already record theirs.

Revision ID: 0051_open_basedir
Revises: 0050_wp_staging
Create Date: 2026-10-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0051_open_basedir"
down_revision: Union[str, None] = "0050_wp_staging"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "open_basedir" not in _columns("websites"):
        with op.batch_alter_table("websites") as batch:
            batch.add_column(sa.Column("open_basedir", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    if "open_basedir" not in _columns("hosting_plans"):
        with op.batch_alter_table("hosting_plans") as batch:
            batch.add_column(sa.Column("open_basedir", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    if "plan_id" not in _columns("users"):
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("plan_id", sa.Integer(), nullable=True))
        # The package a WHMCS account was provisioned with is already known.
        op.execute(
            "UPDATE users SET plan_id = (SELECT plan_id FROM hosting_accounts "
            "WHERE hosting_accounts.user_id = users.id AND hosting_accounts.plan_id IS NOT NULL "
            "ORDER BY hosting_accounts.id DESC LIMIT 1) "
            "WHERE plan_id IS NULL"
        )


def downgrade() -> None:
    if "plan_id" in _columns("users"):
        with op.batch_alter_table("users") as batch:
            batch.drop_column("plan_id")
    if "open_basedir" in _columns("hosting_plans"):
        with op.batch_alter_table("hosting_plans") as batch:
            batch.drop_column("open_basedir")
    if "open_basedir" in _columns("websites"):
        with op.batch_alter_table("websites") as batch:
            batch.drop_column("open_basedir")
