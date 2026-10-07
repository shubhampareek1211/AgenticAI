#!/usr/bin/env bash
set -euo pipefail

# Local whisper.cpp worker. All downloaded/build artifacts remain in ignored .local/.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$ROOT/.local/voice"
SOURCE="$WORK/whisper.cpp"
MODEL_DIR="$WORK/models"
WHISPER_TAG=v1.9.4
WHISPER_COMMIT=927cfce34f31707e17f2bff35c349632fb9e2c3a
MODEL_REV=5359861c739e955e79d9a303bcbc70fb988958b1

usage() {
  cat <<'EOF'
Usage: bash scripts/voice.sh setup [base.en|small.en|small]
       bash scripts/voice.sh serve [base.en|small.en|small]
       bash scripts/voice.sh smoke PATH_TO_16KHZ_MONO_PCM_WAV
       bash scripts/voice.sh synthesize [PATH_TO_WAV]  # macOS only

setup downloads a checksum-verified model and builds the pinned whisper.cpp server.
serve binds only to 127.0.0.1:8081. Set VOICE_THREADS to adjust worker threads.
EOF
}

model_info() {
  case "$1" in
    base.en) MODEL_SHA256=a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002 ;;
    small.en) MODEL_SHA256=c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d ;;
    small) MODEL_SHA256=1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b ;;
    *) echo "Unsupported model: $1" >&2; usage >&2; exit 2 ;;
  esac
  MODEL_FILE="$MODEL_DIR/ggml-$1.bin"
}

verify_model() {
  [ -s "$MODEL_FILE" ] || return 1
  if command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$MODEL_FILE" | awk '{print $1}')"
  else
    actual="$(sha256sum "$MODEL_FILE" | awk '{print $1}')"
  fi
  [ "$actual" = "$MODEL_SHA256" ]
}

download_model() {
  mkdir -p "$MODEL_DIR"
  if verify_model; then
    echo "Verified $MODEL_FILE"
    return
  fi
  rm -f "$MODEL_FILE"
  tmp="$(mktemp "$MODEL_DIR/.model.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  curl --fail --location --retry 3 --output "$tmp" \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/$MODEL_REV/ggml-${model}.bin"
  mv "$tmp" "$MODEL_FILE"
  trap - EXIT
  if ! verify_model; then
    rm -f "$MODEL_FILE"
    echo "Model SHA-256 mismatch; discarded download" >&2
    exit 1
  fi
  echo "Verified $MODEL_FILE"
}

build_source() {
  command -v git >/dev/null || { echo 'git is required' >&2; exit 1; }
  command -v cmake >/dev/null || { echo 'cmake is required' >&2; exit 1; }
  mkdir -p "$WORK"
  if [ ! -d "$SOURCE/.git" ]; then
    git clone --depth 1 --branch "$WHISPER_TAG" \
      https://github.com/ggml-org/whisper.cpp.git "$SOURCE"
  fi
  [ "$(git -C "$SOURCE" describe --tags --exact-match HEAD)" = "$WHISPER_TAG" ] || {
    echo "Source at $SOURCE is not $WHISPER_TAG" >&2; exit 1;
  }
  [ "$(git -C "$SOURCE" rev-parse HEAD)" = "$WHISPER_COMMIT" ] || {
    echo "Unexpected whisper.cpp commit at $SOURCE" >&2; exit 1;
  }
  git -C "$SOURCE" diff --quiet && git -C "$SOURCE" diff --cached --quiet || {
    echo "Tracked whisper.cpp source has local changes" >&2; exit 1;
  }
  cmake -S "$SOURCE" -B "$SOURCE/build" -DCMAKE_BUILD_TYPE=Release \
    -DWHISPER_BUILD_SERVER=ON -DWHISPER_BUILD_TESTS=OFF
  cmake --build "$SOURCE/build" --config Release --target whisper-server -j 4
}

command="${1:-}"
case "$command" in
  setup)
    model="${2:-base.en}"; model_info "$model"
    build_source
    download_model
    ;;
  serve)
    model="${2:-base.en}"; model_info "$model"
    binary="$SOURCE/build/bin/whisper-server"
    [ -x "$binary" ] || { echo 'Run setup first: server binary is missing' >&2; exit 1; }
    verify_model || { echo 'Run setup first: model missing or checksum mismatch' >&2; exit 1; }
    threads="${VOICE_THREADS:-4}"
    [[ "$threads" =~ ^[1-9][0-9]*$ ]] || { echo 'VOICE_THREADS must be a positive integer' >&2; exit 2; }
    exec "$binary" --host 127.0.0.1 --port 8081 --model "$MODEL_FILE" \
      --threads "$threads"
    ;;
  smoke)
    wav="${2:-}"
    [ -f "$wav" ] || { echo 'Provide a WAV file' >&2; exit 2; }
    curl --fail --show-error --silent http://127.0.0.1:8081/inference \
      -F "file=@${wav};type=audio/wav" -F "language=${VOICE_LANGUAGE:-en}" -F 'response_format=json'
    printf '\n'
    ;;
  synthesize)
    command -v say >/dev/null && command -v afconvert >/dev/null || {
      echo 'synthesize requires macOS say and afconvert' >&2; exit 1;
    }
    output="${2:-$WORK/cricket-sample.wav}"
    mkdir -p "$(dirname "$output")"
    aiff="$(mktemp "$WORK/.speech.XXXXXX.aiff")"
    trap 'rm -f "$aiff"' EXIT
    say -o "$aiff" 'Compare Virat Kohli and Joe Root ODI runs in 2019.'
    afconvert -f WAVE -d LEI16@16000 -c 1 "$aiff" "$output"
    echo "$output"
    ;;
  *) usage; [ -z "$command" ] || exit 2 ;;
esac
