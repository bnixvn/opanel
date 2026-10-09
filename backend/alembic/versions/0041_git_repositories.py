"""git repositories in a hosting account's home, and their operations

Revision ID: 0041_git_repositories
Revises: 0040_drop_unused_pool_columns
Create Date: 2026-10-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0041_git_repositories"
down_revision: Union[str, None] = "0040_drop_unused_pool_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("git_repositories"):
        op.create_table(
            "git_repositories",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("path", sa.String(length=512), nullable=False),
            sa.Column("remote_url", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("branch", sa.String(length=100), nullable=False, server_default="main"),
            sa.Column("auth_type", sa.String(length=10), nullable=False, server_default="none"),
            sa.Column("https_username", sa.String(length=100), nullable=False, server_default=""),
            sa.Column("https_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("ssh_private_key", sa.Text(), nullable=False, server_default=""),
            sa.Column("ssh_public_key", sa.Text(), nullable=False, server_default=""),
            sa.Column("deploy_commands", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("php_version", sa.String(length=8), nullable=False, server_default=""),
            sa.Column("webhook_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("webhook_token", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("ready", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_git_repositories_owner_id", "git_repositories", ["owner_id"])
        op.create_index("ix_git_repositories_path", "git_repositories", ["path"], unique=True)
    if not _has_table("git_operations"):
        op.create_table(
            "git_operations",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("repository_id", sa.Integer(), sa.ForeignKey("git_repositories.id"), nullable=False),
            sa.Column("action", sa.String(length=16), nullable=False),
            sa.Column("trigger", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
            sa.Column("commit_before", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("commit_after", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("log", sa.Text(), nullable=False, server_default=""),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_git_operations_repository_id", "git_operations", ["repository_id"])


def downgrade() -> None:
    if _has_table("git_operations"):
        op.drop_table("git_operations")
    if _has_table("git_repositories"):
        op.drop_table("git_repositories")
