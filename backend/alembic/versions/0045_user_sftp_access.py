"""Main SFTP login per account: on or off, panel password or its own

An administrator or reseller turns an account's main SFTP login off and on,
and it signs in with the panel password or a password of its own (as
SNPanel's, 2026-10-17). `sftp_sync_pending` marks one switched back to the
panel password, which the panel only learns at the account's next sign-in.

Revision ID: 0045_user_sftp_access
Revises: 0044_backup_name_style
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0045_user_sftp_access"
down_revision: Union[str, None] = "0044_backup_name_style"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("sftp_enabled", sa.true()),
    ("sftp_own_password", sa.false()),
    ("sftp_sync_pending", sa.false()),
)


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _columns("users")
    with op.batch_alter_table("users") as batch:
        for name, default in COLUMNS:
            if name not in have:
                batch.add_column(sa.Column(name, sa.Boolean(), nullable=False, server_default=default))


def downgrade() -> None:
    have = _columns("users")
    with op.batch_alter_table("users") as batch:
        for name, _ in COLUMNS:
            if name in have:
                batch.drop_column(name)
