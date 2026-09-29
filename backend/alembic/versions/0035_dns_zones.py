"""dns_zones: which account owns which zone (DNS Manager addon)

The records themselves live in PowerDNS; the panel keeps ownership, which is
what lets a customer manage the zones of their own domains and nothing else.

Revision ID: 0035_dns_zones
Revises: 0034_mail_relay_dns
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035_dns_zones"
down_revision: Union[str, None] = "0034_mail_relay_dns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("dns_zones"):
        op.create_table(
            "dns_zones",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=253), nullable=False),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_dns_zones_id", "dns_zones", ["id"])
        op.create_index("ix_dns_zones_name", "dns_zones", ["name"], unique=True)
        op.create_index("ix_dns_zones_owner_id", "dns_zones", ["owner_id"])


def downgrade() -> None:
    if _has_table("dns_zones"):
        op.drop_table("dns_zones")
