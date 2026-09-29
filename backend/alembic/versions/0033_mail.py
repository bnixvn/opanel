"""mail: mail_domains, mailboxes, mail_forwarders; users.mailbox_limit

The Email addon (Exim, Dovecot, Rspamd and the webmail). The panel's database
is the source of truth for mail domains, mailboxes and forwarders; the root
helper renders them into the maps Exim and Dovecot read (mail-sync).

users.mailbox_limit caps mailboxes per account across all its domains, NOT NULL
with a default like the other limits. 0 means unlimited.

Revision ID: 0033_mail
Revises: 0032_notifications
Create Date: 2026-09-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033_mail"
down_revision: Union[str, None] = "0032_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_MAILBOX_LIMIT = 10


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return column in {row["name"] for row in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("users", "mailbox_limit"):
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(sa.Column("mailbox_limit", sa.Integer(), nullable=False,
                                          server_default=str(DEFAULT_MAILBOX_LIMIT)))
    if not _has_table("mail_domains"):
        op.create_table(
            "mail_domains",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("domain", sa.String(length=255), nullable=False),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("catch_all", sa.String(length=255), nullable=True),
            sa.Column("dkim_public", sa.Text(), nullable=True),
            sa.Column("webmail_host", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_mail_domains_id", "mail_domains", ["id"])
        op.create_index("ix_mail_domains_domain", "mail_domains", ["domain"], unique=True)
        op.create_index("ix_mail_domains_owner_id", "mail_domains", ["owner_id"])
    if not _has_table("mailboxes"):
        op.create_table(
            "mailboxes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("domain_id", sa.Integer(), sa.ForeignKey("mail_domains.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("local_part", sa.String(length=64), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("quota_mb", sa.Integer(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("domain_id", "local_part", name="uq_mailboxes_domain_local"),
        )
        op.create_index("ix_mailboxes_id", "mailboxes", ["id"])
        op.create_index("ix_mailboxes_domain_id", "mailboxes", ["domain_id"])
    if not _has_table("mail_forwarders"):
        op.create_table(
            "mail_forwarders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("domain_id", sa.Integer(), sa.ForeignKey("mail_domains.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("local_part", sa.String(length=64), nullable=False),
            sa.Column("destinations", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("domain_id", "local_part", name="uq_mail_forwarders_domain_local"),
        )
        op.create_index("ix_mail_forwarders_id", "mail_forwarders", ["id"])
        op.create_index("ix_mail_forwarders_domain_id", "mail_forwarders", ["domain_id"])


def downgrade() -> None:
    for table in ("mail_forwarders", "mailboxes", "mail_domains"):
        if _has_table(table):
            op.drop_table(table)
    if _has_column("users", "mailbox_limit"):
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column("mailbox_limit")
