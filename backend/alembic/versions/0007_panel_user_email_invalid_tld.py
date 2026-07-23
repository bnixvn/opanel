"""move generated panel user emails to the RFC 2606 .invalid TLD

Revision ID: 0007_panel_user_email_invalid_tld
Revises: 0006_backup_schedule_user_sets
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0007_panel_user_email_invalid_tld"
down_revision: Union[str, None] = "0006_backup_schedule_user_sets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # @users.opanel.vn / @users.opanel.test were synthetic addresses for
    # auto-created panel-only users; opanel.vn is a real domain and opanel.test
    # is reserved but ambiguous. Move every synthetic address to the reserved
    # .invalid TLD (RFC 2606) so it can never accidentally route mail.
    op.execute(
        "UPDATE users SET email = REPLACE(email, '@users.opanel.vn', '@users.opanel.invalid') "
        "WHERE email LIKE '%@users.opanel.vn'"
    )
    op.execute(
        "UPDATE users SET email = REPLACE(email, '@users.opanel.test', '@users.opanel.invalid') "
        "WHERE email LIKE '%@users.opanel.test'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE users SET email = REPLACE(email, '@users.opanel.invalid', '@users.opanel.vn') "
        "WHERE email LIKE '%@users.opanel.invalid'"
    )
