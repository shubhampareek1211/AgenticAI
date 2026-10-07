import json
import zipfile


def archive(tmp_path, payload, match_id="990001"):
    path = tmp_path / "matches.zip"
    with zipfile.ZipFile(path, "w") as output:
        output.writestr(f"{match_id}.json", json.dumps(payload))
    return path


def synthetic_match():
    def ball(
        runs=0,
        extras=None,
        non_boundary=False,
        wickets=None,
        batter="V Kohli",
        non_striker="RG Sharma",
        bowler="JJ Bumrah",
    ):
        extras = extras or {}
        delivery = {
            "batter": batter,
            "non_striker": non_striker,
            "bowler": bowler,
            "runs": {
                "batter": runs,
                "extras": sum(extras.values()),
                "total": runs + sum(extras.values()),
            },
        }
        if extras:
            delivery["extras"] = extras
        if non_boundary:
            delivery["runs"]["non_boundary"] = True
        if wickets:
            delivery["wickets"] = wickets
        return delivery

    return {
        "meta": {"data_version": "1.2.0", "created": "2026-01-01", "revision": 1},
        "info": {
            "gender": "male",
            "team_type": "international",
            "match_type": "ODI",
            "balls_per_over": 6,
            "dates": ["2026-01-01"],
            "teams": ["India", "Test XI"],
            "missing": ["reviews", {"powerplays": {"1": ["batting"]}}],
            "players": {
                "India": ["V Kohli", "RG Sharma", "New Batter"],
                "Test XI": ["JJ Bumrah", "Other Batter"],
            },
            "registry": {
                "people": {
                    "V Kohli": "ba607b88",
                    "RG Sharma": "740742ef",
                    "JJ Bumrah": "462411b3",
                    "New Batter": "new00001",
                    "Other Batter": "other001",
                }
            },
        },
        "innings": [
            {
                "team": "India",
                "penalty_runs": {"pre": 5, "post": 1},
                "overs": [
                    {
                        "over": 0,
                        "deliveries": [
                            ball(),
                            ball(extras={"wides": 1}),
                            ball(4, {"noballs": 1}),
                            ball(4, non_boundary=True),
                            ball(extras={"byes": 2}),
                            ball(6),
                            ball(extras={"legbyes": 1}),
                            ball(1, wickets=[{"player_out": "RG Sharma", "kind": "run out"}]),
                        ],
                    },
                    {
                        "over": 1,
                        "deliveries": [
                            ball(2, non_striker="New Batter"),
                            ball(
                                non_striker="New Batter",
                                wickets=[{"player_out": "V Kohli", "kind": "retired hurt"}],
                            ),
                        ],
                    },
                ],
            },
            {
                "team": "Test XI",
                "overs": [
                    {
                        "over": 0,
                        "deliveries": [
                            ball(batter="JJ Bumrah", non_striker="Other Batter", bowler="V Kohli"),
                            ball(
                                6,
                                batter="JJ Bumrah",
                                non_striker="Other Batter",
                                bowler="V Kohli",
                                wickets=[
                                    {
                                        "player_out": "JJ Bumrah",
                                        "kind": "caught",
                                        "fielders": [{"name": "RG Sharma"}],
                                    }
                                ],
                            ),
                        ],
                    }
                ],
            },
        ],
    }
