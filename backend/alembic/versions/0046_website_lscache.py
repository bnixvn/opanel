"""lscache_enabled on websites

LiteSpeed's page cache looked up for the site, or not, each site with a
cache folder of its own so it can be emptied alone (as SNPanel's, 2026-10-17).
On by default: every site had it, in one shared folder, before.

Revision ID: 0046_website_lscache
Revises: 0045_user_sftp_access
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0046_website_lscache"
down_revision: Union[str, None] = "0045_user_sftp_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "lscache_enabled" not in _columns("websites"):
        with op.batch_alter_table("websites") as batch:
            batch.add_column(sa.Column("lscache_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    if "lscache_enabled" in _columns("websites"):
        with op.batch_alter_table("websites") as batch:
            batch.drop_column("lscache_enabled")
