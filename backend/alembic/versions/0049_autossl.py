"""autossl_sites

AutoSSL's state of each website: on or off, what its names resolved to,
when it was last and is next checked, and how many times it failed
(services/autossl.py, 2026-10-17).

Revision ID: 0049_autossl
Revises: 0048_restore_jobs
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0049_autossl"
down_revision: Union[str, None] = "0048_restore_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "autossl_sites" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "autossl_sites",
        sa.Column("website_id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False, server_default=""),
        sa.Column("names", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_check", sa.DateTime(), nullable=True),
        sa.Column("next_check", sa.DateTime(), nullable=True),
        sa.Column("last_issued_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    if "autossl_sites" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("autossl_sites")
