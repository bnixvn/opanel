"""sftp: sftp_accounts

Extra SFTP logins a hosting account creates for one of its folders. The
account's own login is its Linux user and needs no row.

Revision ID: 0031_sftp_accounts
Revises: 0030_mcp_tokens
Create Date: 2026-09-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0031_sftp_accounts"
down_revision: Union[str, None] = "0030_mcp_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _has_table("sftp_accounts"):
        return
    op.create_table(
        "sftp_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("website_id", sa.Integer(), sa.ForeignKey("websites.id"), nullable=True),
        sa.Column("directory", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sftp_accounts_owner_id", "sftp_accounts", ["owner_id"])
    op.create_index("ix_sftp_accounts_username", "sftp_accounts", ["username"], unique=True)
    op.create_index("ix_sftp_accounts_website_id", "sftp_accounts", ["website_id"])


def downgrade() -> None:
    if not _has_table("sftp_accounts"):
        return
    op.drop_index("ix_sftp_accounts_website_id", table_name="sftp_accounts")
    op.drop_index("ix_sftp_accounts_username", table_name="sftp_accounts")
    op.drop_index("ix_sftp_accounts_owner_id", table_name="sftp_accounts")
    op.drop_table("sftp_accounts")
