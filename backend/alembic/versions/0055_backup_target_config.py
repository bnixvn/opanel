"""A backup destination's settings when an addon brings its kind

Google Drive and any other addon destination (services/destinations.py) keep
their settings - an OAuth token, a folder - as one encrypted JSON blob here;
SFTP and S3 keep their own columns (operator, 2026-11-16).

Revision ID: 0055_backup_target_config
Revises: 0054_user_php_disable_functions
Create Date: 2026-11-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0055_backup_target_config"
down_revision: Union[str, None] = "0054_user_php_disable_functions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "config" in _columns("backup_targets"):
        return
    with op.batch_alter_table("backup_targets") as batch:
        batch.add_column(sa.Column("config", sa.Text(), nullable=True))


def downgrade() -> None:
    if "config" not in _columns("backup_targets"):
        return
    with op.batch_alter_table("backup_targets") as batch:
        batch.drop_column("config")
