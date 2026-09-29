"""mail_domains gain relay and dns_custom

relay picks the outgoing relay (smarthost) a domain's mail leaves through --
the server default, none ("direct") or one of the relays the administrator set
up. dns_custom holds the owner's own SPF and DMARC values and extra records,
which a relay usually asks for.

Revision ID: 0034_mail_relay_dns
Revises: 0033_mail
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_mail_relay_dns"
down_revision: Union[str, None] = "0033_mail"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    return column in {row["name"] for row in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    with op.batch_alter_table("mail_domains") as batch_op:
        if not _has_column("mail_domains", "relay"):
            batch_op.add_column(sa.Column("relay", sa.String(length=40), nullable=False, server_default=""))
        if not _has_column("mail_domains", "dns_custom"):
            batch_op.add_column(sa.Column("dns_custom", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("mail_domains") as batch_op:
        if _has_column("mail_domains", "dns_custom"):
            batch_op.drop_column("dns_custom")
        if _has_column("mail_domains", "relay"):
            batch_op.drop_column("relay")
