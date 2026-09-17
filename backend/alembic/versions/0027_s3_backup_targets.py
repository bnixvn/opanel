"""backup targets gain a kind, and S3-compatible columns

sftp_backup_targets held the only kind of destination there was. Adding a
second table would have meant two nullable foreign keys on backup_schedules
and two lists in the panel, so the table is renamed and given a `kind` column
instead. Existing rows are SFTP.

The S3 columns are NOT NULL with defaults rather than nullable: an SFTP row
never reads them, and a default keeps the ORM from having to treat "" and NULL
as the same thing everywhere.

Revision ID: 0027_s3_backup_targets
Revises: 0026_drop_website_http_flood
Create Date: 2026-09-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027_s3_backup_targets"
down_revision: Union[str, None] = "0026_drop_website_http_flood"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("sftp_backup_targets", "backup_targets")
    with op.batch_alter_table("backup_targets") as batch_op:
        batch_op.add_column(sa.Column("kind", sa.String(16), nullable=False, server_default="sftp"))
        batch_op.add_column(sa.Column("s3_endpoint", sa.String(255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("s3_region", sa.String(64), nullable=False, server_default="us-east-1"))
        batch_op.add_column(sa.Column("s3_bucket", sa.String(255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("s3_access_key", sa.String(255), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("s3_secret_key", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("s3_use_path_style", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("backup_targets") as batch_op:
        for column in (
            "s3_use_path_style",
            "s3_secret_key",
            "s3_access_key",
            "s3_bucket",
            "s3_region",
            "s3_endpoint",
            "kind",
        ):
            batch_op.drop_column(column)
    op.rename_table("backup_targets", "sftp_backup_targets")
