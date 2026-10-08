"""Isolated profile fetch and identity lookup. Never infer mappings from names."""

import os

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from cricket.models import ExternalPlayerID, Player, utcnow

PROFILE_URL = "https://site.web.api.espn.com/apis/common/v3/sports/cricket/athletes/{player_id}"


class PlayerResolutionError(ValueError):
    pass


def profile_identity(profile: dict) -> tuple[str, dict]:
    if not isinstance(profile, dict):
        raise PlayerResolutionError("ESPN profile must be a JSON object.")
    athlete = profile.get("athlete", profile)
    if not isinstance(athlete, dict):
        raise PlayerResolutionError("ESPN profile has no valid athlete record.")
    external_id = athlete.get("id")
    if external_id is None or not str(external_id).isdigit():
        raise PlayerResolutionError("ESPN profile has no valid stable athlete ID.")
    return str(external_id), athlete


def resolve_espn_profile(session: Session, profile: dict) -> Player:
    external_id, _ = profile_identity(profile)
    mapping = session.get(ExternalPlayerID, ("espn", external_id))
    if not mapping:
        raise PlayerResolutionError(
            "ESPN athlete has no Cricsheet Register mapping; no name match guessed."
        )
    mapping.profile = profile
    mapping.profile_retrieved_at = utcnow()
    return session.get(Player, mapping.player_id)


def find_players(session: Session, name: str) -> list[Player]:
    """Name lookup returns all candidates; it does not assign external identities."""
    folded = name.strip().casefold()
    if not folded:
        return []
    # Register is small. Casefold handles Unicode without a database collation assumption.
    return [
        player
        for player in session.scalars(select(Player).order_by(Player.id))
        if folded
        in {value.casefold() for value in [player.name, player.unique_name, *player.aliases]}
    ]


def fetch_profile(external_id: str) -> dict:
    if not external_id.isdigit():
        raise PlayerResolutionError("ESPN athlete ID must be numeric.")
    try:
        timeout = float(os.environ.get("ESPN_TIMEOUT_SECONDS", "10"))
        if timeout <= 0:
            raise ValueError
    except ValueError:
        raise PlayerResolutionError("ESPN_TIMEOUT_SECONDS must be a positive number.") from None
    try:
        response = requests.get(PROFILE_URL.format(player_id=external_id), timeout=timeout)
        response.raise_for_status()
        profile = response.json()
    except (requests.RequestException, ValueError):
        raise PlayerResolutionError(
            "ESPN profile unavailable; retry later or supply a saved profile JSON."
        ) from None
    if not isinstance(profile, dict) or profile_identity(profile)[0] != external_id:
        raise PlayerResolutionError("ESPN returned an unexpected athlete identity.")
    return profile
