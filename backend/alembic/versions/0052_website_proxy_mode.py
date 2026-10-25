"""a website can be a reverse proxy to any http(s) address

Website mode "proxy" (operator, 2026-10-25, as BPanel's Nginx proxy):
proxy_url is where OpenLiteSpeed forwards, proxy_preserve_host whether the
visitor's Host header goes along.

Revision ID: 0052_website_proxy_mode
Revises: 0051_open_basedir
Create Date: 2026-10-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0052_website_proxy_mode"
down_revision: Union[str, None] = "0051_open_basedir"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("websites")
    with op.batch_alter_table("websites") as batch:
        if "proxy_url" not in columns:
            batch.add_column(sa.Column("proxy_url", sa.String(500), nullable=False, server_default=""))
        if "proxy_preserve_host" not in columns:
            batch.add_column(sa.Column("proxy_preserve_host", sa.Boolean(), nullable=False, server_default=sa.text("1")))


def downgrade() -> None:
    columns = _columns("websites")
    with op.batch_alter_table("websites") as batch:
        for name in ("proxy_url", "proxy_preserve_host"):
            if name in columns:
                batch.drop_column(name)
