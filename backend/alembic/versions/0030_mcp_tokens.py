"""mcp: mcp_tokens

Credentials for the MCP endpoint (/api/mcp). Each belongs to one account and
acts as it, so an end user's token is scoped to that user's own websites; the
separate api_tokens table is the provisioning machine credential and has no
owner at all.

Revision ID: 0030_mcp_tokens
Revises: 0029_webauthn_credentials
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_mcp_tokens"
down_revision: Union[str, None] = "0029_webauthn_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _has_table("mcp_tokens"):
        return
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("can_write", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_mcp_tokens_user_id", "mcp_tokens", ["user_id"])
    op.create_index("ix_mcp_tokens_token_hash", "mcp_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    if not _has_table("mcp_tokens"):
        return
    op.drop_index("ix_mcp_tokens_token_hash", table_name="mcp_tokens")
    op.drop_index("ix_mcp_tokens_user_id", table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
