"""restore_jobs

Each restore as a record: its archives and how each went, kept past a
restart, so a failed or interrupted one can be run again (as SNPanel's,
2026-10-17).

Revision ID: 0048_restore_jobs
Revises: 0047_website_custom_rules
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0048_restore_jobs"
down_revision: Union[str, None] = "0047_website_custom_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "restore_jobs" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "restore_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False, server_default="local"),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("source_label", sa.String(255), nullable=False, server_default=""),
        sa.Column("overwrite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("items", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("retry_of", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    if "restore_jobs" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("restore_jobs")
