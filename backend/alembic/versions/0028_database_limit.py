"""users and hosting_plans gain a database_limit

POST /api/databases had no count quota of any kind, while both sibling tenant
resources are metered -- websites against users.website_limit and bytes against
users.storage_limit_mb, each with a non-zero default -- and HostingPlan could
not express a database limit at all. Each create forks two mysql clients and
ends its DDL batch with FLUSH PRIVILEGES, a globally locking privilege-table
reload whose cost grows with the number of accounts already present, so an
uncapped create path made every subsequent create more expensive for every
tenant sharing the instance.

NOT NULL with a default rather than nullable, matching website_limit: a limit
that can be NULL forces every enforcement site to decide what NULL means, and
Website.linux_user already showed where that leads.

The default of 10 is deliberately above what an ordinary account uses (one per
site, plus a couple standalone) so existing installs do not start refusing
legitimate creates on upgrade. 0 means unlimited, as it does for
storage_limit_mb.

Revision ID: 0028_database_limit
Revises: 0027_s3_backup_targets
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028_database_limit"
down_revision: Union[str, None] = "0027_s3_backup_targets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_DATABASE_LIMIT = 10


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {row["name"] for row in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    for table, default in (
        ("users", DEFAULT_DATABASE_LIMIT),
        ("hosting_plans", DEFAULT_DATABASE_LIMIT),
    ):
        if _has_column(table, "database_limit"):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "database_limit",
                    sa.Integer(),
                    nullable=False,
                    server_default=str(default),
                )
            )


def downgrade() -> None:
    for table in ("hosting_plans", "users"):
        if not _has_column(table, "database_limit"):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("database_limit")
