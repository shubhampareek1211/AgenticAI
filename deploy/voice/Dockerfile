# Source, model, and base image are fixed independently. Model integrity is checked
# during the build. Debian package versions are recorded from the image build.
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates cmake curl g++ git make && rm -rf /var/lib/apt/lists/*

ARG WHISPER_TAG=v1.9.4
ARG WHISPER_COMMIT=927cfce34f31707e17f2bff35c349632fb9e2c3a
ARG MODEL=small
ARG MODEL_SHA256=1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b
ARG MODEL_REV=5359861c739e955e79d9a303bcbc70fb988958b1

RUN git clone --depth 1 --branch "$WHISPER_TAG" \
      https://github.com/ggml-org/whisper.cpp.git /src/whisper.cpp && \
    test "$(git -C /src/whisper.cpp describe --tags --exact-match HEAD)" = "$WHISPER_TAG" && \
    test "$(git -C /src/whisper.cpp rev-parse HEAD)" = "$WHISPER_COMMIT" && \
    cmake -S /src/whisper.cpp -B /src/whisper.cpp/build \
      -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_SERVER=ON \
      -DWHISPER_BUILD_TESTS=OFF -DGGML_NATIVE=OFF -DBUILD_SHARED_LIBS=OFF && \
    cmake --build /src/whisper.cpp/build --target whisper-server -j 4

RUN test "$MODEL:$MODEL_SHA256" = \
      'small:1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b' || \
      { echo 'The worker requires the pinned multilingual small model' >&2; exit 1; }; \
    mkdir -p /models && \
    curl --fail --location --retry 3 \
      "https://huggingface.co/ggerganov/whisper.cpp/resolve/$MODEL_REV/ggml-$MODEL.bin" \
      -o /models/model.bin && \
    echo "$MODEL_SHA256  /models/model.bin" | sha256sum -c -

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 libgomp1 && rm -rf /var/lib/apt/lists/* && \
    groupadd --system cricketvoice && useradd --system --gid cricketvoice --home-dir /nonexistent cricketvoice
COPY --from=build /src/whisper.cpp/build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=build /src/whisper.cpp/LICENSE /usr/share/doc/whisper.cpp/LICENSE
COPY --from=build /models/model.bin /models/model.bin
COPY deploy/voice/start-whisper.sh /usr/local/bin/start-whisper
RUN chmod 0755 /usr/local/bin/start-whisper
USER cricketvoice
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/start-whisper"]
