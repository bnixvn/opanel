"""resource limits: CPU, memory, processes and disk I/O per account

The Resource limits addon (2026-10-04) puts every hosting account in a
systemd slice of its own and these are its limits; 0 = unlimited, which is
what every existing account and plan starts with. group_* are a reseller's
caps on its whole group - its own account and all its customers together.

Revision ID: 0039_resource_limits
Revises: 0038_reseller_oversell
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0039_resource_limits"
down_revision: Union[str, None] = "0038_reseller_oversell"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LIMITS = ("cpu_percent", "memory_mb", "process_limit", "io_read_mbps", "io_write_mbps")
USER_COLUMNS = LIMITS + tuple(f"group_{name}" for name in LIMITS)


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, columns in (("users", USER_COLUMNS), ("hosting_plans", LIMITS)):
        existing = _columns(table)
        missing = [name for name in columns if name not in existing]
        if missing:
            with op.batch_alter_table(table) as batch:
                for name in missing:
                    batch.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for table, columns in (("users", USER_COLUMNS), ("hosting_plans", LIMITS)):
        existing = _columns(table)
        with op.batch_alter_table(table) as batch:
            for name in columns:
                if name in existing:
                    batch.drop_column(name)
