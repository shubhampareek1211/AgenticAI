"""Check the assignment submission file before publishing the repository."""

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_AUTHORS = ["sp4553@columbia.edu"]
NON_APP_SERVICE_PREFIXES = (
    "agenticai-git-",  # Existing IAP-protected application.
    "agenticai-voice-pilot-",
    "agenticai-kokoro-pilot-",
)


def validate(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON from {path}: {exc}") from exc

    if not isinstance(data, dict) or set(data) != {"deploy_url", "authors"}:
        raise ValueError("submission.json must contain only deploy_url and authors")
    if data["authors"] != EXPECTED_AUTHORS:
        raise ValueError("authors must list the confirmed sole author: sp4553@columbia.edu")

    url = data["deploy_url"]
    if not isinstance(url, str):
        raise TypeError("deploy_url must be a string")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"deploy_url is malformed: {exc}") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in url)
    ):
        raise ValueError(
            "deploy_url must be an HTTPS website origin with no path, credentials, or query"
        )
    if parsed.hostname.lower().startswith(NON_APP_SERVICE_PREFIXES):
        raise ValueError("deploy_url points to the legacy app or a private speech worker")
    return url


def main() -> int:
    if len(sys.argv) > 2:
        print("Usage: python3 scripts/validate_submission.py [submission.json]", file=sys.stderr)
        return 2
    path = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else Path(__file__).resolve().parents[1] / "submission.json"
    )
    try:
        url = validate(path)
    except (TypeError, ValueError) as exc:
        print(f"Invalid submission: {exc}", file=sys.stderr)
        return 1
    print(f"Submission format valid for {EXPECTED_AUTHORS[0]}: {url}")
    print("Check that the URL serves the signed-in agent in a browser before submitting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
