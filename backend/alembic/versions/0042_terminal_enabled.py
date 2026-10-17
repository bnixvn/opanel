"""terminal_enabled on accounts and packages

The browser terminal is part of what an account is sold, off unless its
package or the administrator turns it on (operator, 2026-10-17).

Revision ID: 0042_terminal_enabled
Revises: 0041_git_repositories
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0042_terminal_enabled"
down_revision: Union[str, None] = "0041_git_repositories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("users", "hosting_plans")


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in TABLES:
        if "terminal_enabled" not in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column("terminal_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for table in TABLES:
        if "terminal_enabled" in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column("terminal_enabled")
