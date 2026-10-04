"""drop the reseller share columns 1.29.0 stopped reading

0037 gave a reseller's share five totals. Since 0038 (OPanel 1.29.0) a share
is customers and disk only; the website, database and mailbox totals stayed in
the table, unread. They go now, values and all - nothing has looked at them
since 1.29.0, and keeping them only invites code to start reading them again.

ALTER TABLE ... DROP COLUMN (SQLite 3.35+; Ubuntu 22.04 ships 3.37) rather
than a batch copy of the users table, which every other table points at.

Revision ID: 0040_drop_unused_pool_columns
Revises: 0039_resource_limits
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0040_drop_unused_pool_columns"
down_revision: Union[str, None] = "0039_resource_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UNUSED = ("pool_website_limit", "pool_database_limit", "pool_mailbox_limit")


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}


def upgrade() -> None:
    present = _columns()
    for name in UNUSED:
        if name in present:
            op.drop_column("users", name)


def downgrade() -> None:
    present = _columns()
    for name in UNUSED:
        if name not in present:
            op.add_column("users", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
