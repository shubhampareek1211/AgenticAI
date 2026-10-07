"""Download public artifacts atomically and retain checksum/retrieval provenance."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

SOURCE_URLS = {
    "people.csv": "https://cricsheet.org/register/people.csv",
    "names.csv": "https://cricsheet.org/register/names.csv",
    "odis_male_json.zip": "https://cricsheet.org/downloads/odis_male_json.zip",
    "t20s_male_json.zip": "https://cricsheet.org/downloads/t20s_male_json.zip",
}
FORMAT_FILES = {"odi": "odis_male_json.zip", "t20i": "t20s_male_json.zip"}


class SourceError(ValueError):
    pass


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_metadata(path: Path) -> dict:
    actual = checksum(path)
    sidecar = path.with_name(path.name + ".metadata.json")
    if sidecar.exists():
        metadata = json.loads(sidecar.read_text())
        if metadata.get("checksum") != actual:
            raise SourceError(
                f"Checksum mismatch for {path.name}; download it again with --refresh."
            )
        return metadata
    # Local fixtures have a known content revision but no invented download time.
    return {
        "source_url": SOURCE_URLS.get(path.name, path.resolve().as_uri()),
        "checksum": actual,
        "source_revision": f"sha256:{actual}",
        "downloaded_at": None,
    }


def download(directory: Path, formats: list[str], refresh: bool = False) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    for name in ["people.csv", "names.csv", *(FORMAT_FILES[f] for f in formats)]:
        path = directory / name
        if path.exists() and not refresh:
            results.append(artifact_metadata(path))
            continue
        temporary = path.with_name(path.name + ".part")
        url = SOURCE_URLS[name]
        try:
            with requests.get(url, stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                digest = hashlib.sha256()
                with temporary.open("wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        if chunk:
                            stream.write(chunk)
                            digest.update(chunk)
                if temporary.stat().st_size == 0:
                    raise SourceError(f"Empty download for {name}.")
                metadata = {
                    "source_url": url,
                    "checksum": digest.hexdigest(),
                    "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "source_revision": (
                        response.headers.get("ETag")
                        or response.headers.get("Last-Modified")
                        or f"sha256:{digest.hexdigest()}"
                    ),
                }
            temporary.replace(path)
            sidecar = path.with_name(path.name + ".metadata.json")
            sidecar_tmp = sidecar.with_name(sidecar.name + ".part")
            sidecar_tmp.write_text(json.dumps(metadata, indent=2) + "\n")
            sidecar_tmp.replace(sidecar)
            results.append(metadata)
        except requests.RequestException:
            raise SourceError(
                f"Could not download {name}; retry or supply a local source directory."
            ) from None
        finally:
            temporary.unlink(missing_ok=True)
    return results
