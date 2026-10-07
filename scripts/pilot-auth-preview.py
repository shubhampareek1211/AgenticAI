"""Run the real Firebase login against the local app/database on loopback only."""

import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit


def speech_environment(path: Path) -> dict[str, str]:
    config = json.loads(path.read_text())
    env = {}
    for key, prefix in (("stt_url", "STT"), ("tts_url", "TTS")):
        url = config.get(key, "").rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
        ):
            raise ValueError(f"{key} must be an HTTPS service origin")
        env.update({f"{prefix}_BASE_URL": url, f"{prefix}_AUDIENCE": url})
        env[f"{prefix}_AUTH_MODE"] = "google_id_token"
    credentials = Path(config["credentials_file"]).expanduser().resolve()
    if json.loads(credentials.read_text()).get("type") != "impersonated_service_account":
        raise ValueError("Use a local impersonated service-account credential file, not a key")
    env.update(
        GOOGLE_APPLICATION_CREDENTIALS=str(credentials),
        VOICE_ENABLED="true",
        VOICE_LANGUAGES="en,hi,auto",
        TTS_ENABLED="true",
    )
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Firebase web app config JSON")
    parser.add_argument(
        "--speech-config", type=Path, help="Private worker URLs and impersonated ADC"
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    fields = {
        "projectId": "FIREBASE_PROJECT_ID",
        "apiKey": "FIREBASE_WEB_API_KEY",
        "authDomain": "FIREBASE_AUTH_DOMAIN",
        "appId": "FIREBASE_WEB_APP_ID",
    }
    if any(not isinstance(config.get(key), str) or not config[key] for key in fields):
        parser.error("Config must contain projectId, apiKey, authDomain and appId")
    root = Path(__file__).resolve().parents[1]
    server = root / ".venv/bin/uvicorn"
    if not server.is_file() or not (root / "frontend/dist/index.html").is_file():
        parser.error("Install the Python environment and build frontend/dist first")
    env = dict(os.environ)
    env.update({target: config[source] for source, target in fields.items()})
    env.update(
        APP_ENV="pilot",
        GOOGLE_CLOUD_PROJECT=config["projectId"],
        VERTEX_LOCATION=env.get("VERTEX_LOCATION", "global"),
        DATABASE_URL=env.get(
            "DATABASE_URL", "postgresql+psycopg://cricket@127.0.0.1:55432/cricket_analyst"
        ),
        VOICE_ENABLED="false",
        TTS_ENABLED="false",
    )
    if args.speech_config:
        try:
            env.update(speech_environment(args.speech_config))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.error(f"Invalid speech configuration: {type(exc).__name__}")
        if not shutil.which("ffmpeg", path=env.get("PATH")):
            bundled = root / ".local/voice/bin/ffmpeg"
            if not bundled.is_file():
                parser.error("Install FFmpeg before enabling cloud voice")
            env["PATH"] = f"{bundled.parent}{os.pathsep}{env.get('PATH', '')}"
    os.chdir(root)
    print(
        "Firebase sign-in preview: http://localhost:8004 (open email links on this Mac)", flush=True
    )
    # Email action links carry one-time credentials in their query parameters.
    # Do not write those URLs into the Uvicorn access log.
    os.execve(
        server,
        [str(server), "app:app", "--host", "127.0.0.1", "--port", "8004", "--no-access-log"],
        env,
    )


if __name__ == "__main__":
    main()
