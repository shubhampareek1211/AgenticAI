"""Add shared UTC-day voice usage counters for the Columbia pilot.

Revision ID: 0004_voice_daily_usage
Revises: 0003_conversation_summary
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_voice_daily_usage"
down_revision = "0003_conversation_summary"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voice_daily_usage",
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.CheckConstraint("scope IN ('global','user')", name=op.f("ck_voice_daily_usage_scope")),
        sa.CheckConstraint("used >= 1", name=op.f("ck_voice_daily_usage_used_positive")),
        sa.PrimaryKeyConstraint(
            "period_start", "scope", "owner_id", name=op.f("pk_voice_daily_usage")
        ),
    )


def downgrade():
    op.drop_table("voice_daily_usage")
