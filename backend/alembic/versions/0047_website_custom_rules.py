"""custom_rules on websites

A site's own rewrite rules and response headers, from an allowlist, rendered
into its vhost (as SNPanel's custom directives, 2026-10-17). A new column:
the old nginx_custom held nginx directives on some boxes.

Revision ID: 0047_website_custom_rules
Revises: 0046_website_lscache
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0047_website_custom_rules"
down_revision: Union[str, None] = "0046_website_lscache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "custom_rules" not in _columns("websites"):
        with op.batch_alter_table("websites") as batch:
            batch.add_column(sa.Column("custom_rules", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    if "custom_rules" in _columns("websites"):
        with op.batch_alter_table("websites") as batch:
            batch.drop_column("custom_rules")
