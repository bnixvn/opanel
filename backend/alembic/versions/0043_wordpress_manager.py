"""WordPress Manager: the installs found in the websites, and the tasks run on them

Revision ID: 0043_wordpress_manager
Revises: 0042_terminal_enabled
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0043_wordpress_manager"
down_revision: Union[str, None] = "0042_terminal_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("wp_installs"):
        op.create_table(
            "wp_installs",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("website_id", sa.Integer(), sa.ForeignKey("websites.id"), nullable=False),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("rel_path", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("path", sa.String(length=512), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("url", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="installed"),
            sa.Column("core_version", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("core_update", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("plugins_total", sa.Integer(), nullable=True),
            sa.Column("plugins_updates", sa.Integer(), nullable=True),
            sa.Column("themes_total", sa.Integer(), nullable=True),
            sa.Column("themes_updates", sa.Integer(), nullable=True),
            sa.Column("inventory", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("last_checked_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_wp_installs_website_id", "wp_installs", ["website_id"])
        op.create_index("ix_wp_installs_owner_id", "wp_installs", ["owner_id"])
        op.create_index("ix_wp_installs_path", "wp_installs", ["path"], unique=True)
    if not _has_table("wp_jobs"):
        op.create_table(
            "wp_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("install_id", sa.Integer(), nullable=True),
            sa.Column("website_id", sa.Integer(), nullable=True),
            sa.Column("owner_id", sa.Integer(), nullable=True),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("params", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
            sa.Column("stage", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("log", sa.Text(), nullable=False, server_default=""),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_wp_jobs_install_id", "wp_jobs", ["install_id"])
        op.create_index("ix_wp_jobs_owner_id", "wp_jobs", ["owner_id"])


def downgrade() -> None:
    if _has_table("wp_jobs"):
        op.drop_table("wp_jobs")
    if _has_table("wp_installs"):
        op.drop_table("wp_installs")
