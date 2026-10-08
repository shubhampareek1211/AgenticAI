"""Preserve source roster conflicts and exclude their innings without guessing identities.

Revision ID: 0002_roster_teams
Revises: 0001_initial
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_roster_teams"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("pk_match_players", "match_players", type_="primary")
    op.create_primary_key("pk_match_players", "match_players", ["match_id", "player_id", "team"])


def downgrade():
    conflicts = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM match_players GROUP BY match_id, player_id HAVING count(*) > 1)"
        )
    )
    if conflicts:
        raise RuntimeError(
            "Cannot downgrade roster schema while conflicting source memberships exist; no data was deleted."
        )
    op.drop_constraint("pk_match_players", "match_players", type_="primary")
    op.create_primary_key("pk_match_players", "match_players", ["match_id", "player_id"])
