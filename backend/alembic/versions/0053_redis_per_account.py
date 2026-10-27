"""Redis per account: the memory a package or an account may give it, and the switch

The "redis" addon (operator, 2026-10-27, as DirectAdmin has it): each hosting
account may run a Redis of its own on a unix socket in its home. The
administrator sets how much memory it may use per package
(hosting_plans.redis_memory_mb) and per account without one
(users.redis_memory_mb): 64 MB unless set, 0 = not allowed. The account turns
it on and off itself (users.redis_enabled).

Revision ID: 0053_redis_per_account
Revises: 0052_website_proxy_mode
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0053_redis_per_account"
down_revision: Union[str, None] = "0052_website_proxy_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("users")
    with op.batch_alter_table("users") as batch:
        if "redis_memory_mb" not in columns:
            batch.add_column(sa.Column("redis_memory_mb", sa.Integer(), nullable=False, server_default=sa.text("64")))
        if "redis_enabled" not in columns:
            batch.add_column(sa.Column("redis_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    if "redis_memory_mb" not in _columns("hosting_plans"):
        with op.batch_alter_table("hosting_plans") as batch:
            batch.add_column(sa.Column("redis_memory_mb", sa.Integer(), nullable=False, server_default=sa.text("64")))


def downgrade() -> None:
    if "redis_memory_mb" in _columns("hosting_plans"):
        with op.batch_alter_table("hosting_plans") as batch:
            batch.drop_column("redis_memory_mb")
    columns = _columns("users")
    with op.batch_alter_table("users") as batch:
        for name in ("redis_memory_mb", "redis_enabled"):
            if name in columns:
                batch.drop_column(name)
