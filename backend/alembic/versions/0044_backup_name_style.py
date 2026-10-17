"""name_style on backup schedules

How a schedule names its archives: by weekday (what every schedule did
before), by date, with a timestamp, or by the account name alone
(SNPanel's file-name styles, 2026-10-17).

Revision ID: 0044_backup_name_style
Revises: 0043_wordpress_manager
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0044_backup_name_style"
down_revision: Union[str, None] = "0043_wordpress_manager"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "name_style" not in _columns("backup_schedules"):
        with op.batch_alter_table("backup_schedules") as batch:
            batch.add_column(sa.Column("name_style", sa.String(16), nullable=False, server_default="weekday"))


def downgrade() -> None:
    if "name_style" in _columns("backup_schedules"):
        with op.batch_alter_table("backup_schedules") as batch:
            batch.drop_column("name_style")
