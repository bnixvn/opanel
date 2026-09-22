"""passkeys: webauthn_credentials

A second factor that is not an authenticator app. An account uses one or the
other, never both -- that rule lives in app/services/passkeys.py rather than in
a constraint, because it has to be enforced on the way in for both directions
(registering a passkey while TOTP is on, and enabling TOTP while a passkey
exists) and produce a message explaining which one to remove first.

credential_id is UNIQUE across the table, not per user: a credential belongs to
exactly one account, and the login ceremony looks one up before it knows who is
signing in.

Revision ID: 0029_webauthn_credentials
Revises: 0028_database_limit
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_webauthn_credentials"
down_revision: Union[str, None] = "0028_database_limit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _has_table("webauthn_credentials"):
        return
    op.create_table(
        "webauthn_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("credential_id", sa.String(length=512), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(length=64), nullable=False, server_default="Passkey"),
        sa.Column("transports", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_webauthn_credentials_user_id", "webauthn_credentials", ["user_id"]
    )
    op.create_index(
        "ix_webauthn_credentials_credential_id",
        "webauthn_credentials",
        ["credential_id"],
        unique=True,
    )


def downgrade() -> None:
    if not _has_table("webauthn_credentials"):
        return
    op.drop_index("ix_webauthn_credentials_credential_id", table_name="webauthn_credentials")
    op.drop_index("ix_webauthn_credentials_user_id", table_name="webauthn_credentials")
    op.drop_table("webauthn_credentials")
