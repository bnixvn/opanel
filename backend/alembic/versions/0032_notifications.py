"""notifications: notification_preferences, notification_messages

The Notifications addon: each user's own channel and event choices, and the
outbox every email or Telegram message goes through (it doubles as the send
log the admin reads).

Revision ID: 0032_notifications
Revises: 0031_sftp_accounts
Create Date: 2026-09-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032_notifications"
down_revision: Union[str, None] = "0031_sftp_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("notification_preferences"):
        op.create_table(
            "notification_preferences",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("email_enabled", sa.Boolean(), nullable=True),
            sa.Column("telegram_enabled", sa.Boolean(), nullable=True),
            sa.Column("telegram_chat_id", sa.String(length=32), nullable=True),
            sa.Column("muted_events", sa.Text(), nullable=True),
            sa.Column("known_ips", sa.Text(), nullable=True),
            sa.Column("telegram_link_code", sa.String(length=32), nullable=True),
            sa.Column("telegram_link_expires", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    if not _has_table("notification_messages"):
        op.create_table(
            "notification_messages",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("event", sa.String(length=48), nullable=False),
            sa.Column("audience", sa.String(length=8), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("channel", sa.String(length=16), nullable=False),
            sa.Column("recipient", sa.String(length=255), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_notification_messages_created_at", "notification_messages", ["created_at"])
        op.create_index("ix_notification_messages_user_id", "notification_messages", ["user_id"])
        op.create_index("ix_notification_messages_status", "notification_messages", ["status"])


def downgrade() -> None:
    if _has_table("notification_messages"):
        op.drop_index("ix_notification_messages_status", table_name="notification_messages")
        op.drop_index("ix_notification_messages_user_id", table_name="notification_messages")
        op.drop_index("ix_notification_messages_created_at", table_name="notification_messages")
        op.drop_table("notification_messages")
    if _has_table("notification_preferences"):
        op.drop_table("notification_preferences")
