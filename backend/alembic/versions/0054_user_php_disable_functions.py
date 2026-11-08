"""The PHP functions an account's PHP disables

NULL is the default list (services/php_functions.py); a string is the
account's own comma list, "" when nothing is disabled (operator, 2026-11-08).
Per account: LiteSpeed runs one lsphp per Linux user for all of its sites.

Revision ID: 0054_user_php_disable_functions
Revises: 0053_redis_per_account
Create Date: 2026-11-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0054_user_php_disable_functions"
down_revision: Union[str, None] = "0053_redis_per_account"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "php_disable_functions" in _columns("users"):
        return
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("php_disable_functions", sa.Text(), nullable=True))


def downgrade() -> None:
    if "php_disable_functions" not in _columns("users"):
        return
    with op.batch_alter_table("users") as batch:
        batch.drop_column("php_disable_functions")
