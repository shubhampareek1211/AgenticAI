"""Fetch and verify the reviewed Kokoro snapshot at image build time."""

import hashlib
import os
from pathlib import Path

from huggingface_hub import snapshot_download

snapshot = snapshot_download(
    repo_id="hexgrad/Kokoro-82M",
    revision=os.environ["KOKORO_REVISION"],
    allow_patterns=["config.json", "kokoro-v1_0.pth", "voices/af_heart.pt"],
)
actual = Path(snapshot).name
expected = os.environ["KOKORO_REVISION"]
if actual != expected:
    raise SystemExit(f"Kokoro model revision changed: {actual} != {expected}")
weights = Path(snapshot) / "kokoro-v1_0.pth"
digest = hashlib.sha256(weights.read_bytes()).hexdigest()
if digest != "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4":
    raise SystemExit(f"Unexpected model checksum: {digest}")
# Kokoro 0.9.4 requests the default ref. Resolve it to the reviewed snapshot
# in this image's offline cache, without following upstream main at runtime.
refs = Path(snapshot).parent.parent / "refs"
refs.mkdir(exist_ok=True)
(refs / "main").write_text(expected)
