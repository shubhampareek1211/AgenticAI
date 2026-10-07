"""Set up the eSpeak data layout required by the bundled macOS/Linux library."""

import os
from pathlib import Path


def configure_espeak_data(target: Path | None = None) -> None:
    import espeakng_loader

    source = Path(espeakng_loader.get_data_path())
    target = target or Path("/tmp/kokoro-espeak-data")
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        link = target / entry.name
        if not link.exists():
            link.symlink_to(entry)
    # The bundled eSpeak library resolves phontab directly under this path.
    os.environ["ESPEAK_DATA_PATH"] = str(target)
