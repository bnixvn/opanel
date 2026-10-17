"""staging_of on wp_installs

A WordPress made as a staging copy of another (services/wp_extras.py,
2026-10-17): the one it was copied from, which a push goes back to.

Revision ID: 0050_wp_staging
Revises: 0049_autossl
Create Date: 2026-10-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0050_wp_staging"
down_revision: Union[str, None] = "0049_autossl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "staging_of" not in _columns("wp_installs"):
        with op.batch_alter_table("wp_installs") as batch:
            batch.add_column(sa.Column("staging_of", sa.Integer(), nullable=True))


def downgrade() -> None:
    if "staging_of" in _columns("wp_installs"):
        with op.batch_alter_table("wp_installs") as batch:
            batch.drop_column("staging_of")
