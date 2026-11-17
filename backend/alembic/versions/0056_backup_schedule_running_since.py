"""A heartbeat while a backup schedule is running

So the panel can show a schedule as running while the timer (a process of its
own) is backing it up, not only once it finishes. run_schedule sets
running_since when it starts and refreshes it as it works; the page treats a
schedule as running while running_since is recent, so a run killed mid-way
stops showing as running on its own (operator, 2026-11-18).

Revision ID: 0056_backup_schedule_running_since
Revises: 0055_backup_target_config
Create Date: 2026-11-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0056_backup_schedule_running_since"
down_revision: Union[str, None] = "0055_backup_target_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "running_since" in _columns("backup_schedules"):
        return
    with op.batch_alter_table("backup_schedules") as batch:
        batch.add_column(sa.Column("running_since", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if "running_since" not in _columns("backup_schedules"):
        return
    with op.batch_alter_table("backup_schedules") as batch:
        batch.drop_column("running_since")
