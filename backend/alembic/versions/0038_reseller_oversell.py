"""reseller oversell switch: users.pool_oversell

Off, a reseller's share bounds the limits it hands out. On, as cPanel and
DirectAdmin oversell, the limits are not added up and the share bounds what
its accounts actually hold instead.

Revision ID: 0038_reseller_oversell
Revises: 0037_reseller
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0038_reseller_oversell"
down_revision: Union[str, None] = "0037_reseller"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    users = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "pool_oversell" not in users:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("pool_oversell", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("pool_oversell")
