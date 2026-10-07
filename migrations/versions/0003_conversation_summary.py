"""Checkpoint which complete turns are represented by a conversation summary.

Revision ID: 0003_conversation_summary
Revises: 0002_roster_teams
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_conversation_summary"
down_revision = "0002_roster_teams"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "conversations",
        sa.Column(
            "summary_through_turn",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade():
    op.drop_column("conversations", "summary_through_turn")
