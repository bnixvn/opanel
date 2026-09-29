"""dns_zones.managed: the records the panel wrote into a zone (DNS Manager)

Kept per source ("mail:<domain>"), so that when the Email addon's records
change -- another relay, an extra record removed, email turned off -- the panel
takes back the values it wrote itself and leaves the owner's alone.

Revision ID: 0036_dns_managed
Revises: 0035_dns_zones
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0036_dns_managed"
down_revision: Union[str, None] = "0035_dns_zones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "managed" not in _columns("dns_zones"):
        with op.batch_alter_table("dns_zones") as batch:
            batch.add_column(sa.Column("managed", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    if "managed" in _columns("dns_zones"):
        with op.batch_alter_table("dns_zones") as batch:
            batch.drop_column("managed")
