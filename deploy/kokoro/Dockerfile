FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/models/cache HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 KOKORO_THREADS=2

RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng libsndfile1 ca-certificates && rm -rf /var/lib/apt/lists/*

# Use a CPU-only PyTorch wheel; the default Linux wheel pulls in CUDA libraries.
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir kokoro==0.9.4 'transformers>=4.51,<5' \
      fastapi==0.115.12 uvicorn==0.34.2 \
      numpy==1.26.4 'huggingface-hub>=0.34,<1' && \
    pip install --no-cache-dir \
      https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

ARG KOKORO_REVISION=f3ff3571791e39611d31c381e3a41a3af07b4987
ENV KOKORO_REVISION=${KOKORO_REVISION}
COPY deploy/kokoro/download_model.py /tmp/download_model.py
RUN HF_HUB_OFFLINE=0 python /tmp/download_model.py

WORKDIR /app
COPY deploy/kokoro/server.py deploy/kokoro/runtime.py /app/
COPY scripts/kokoro_api_smoke.py /app/smoke.py
RUN pip freeze > /app/requirements-resolved.txt
RUN useradd --system --uid 10001 --home-dir /app kokoro && \
    chown -R 10001 /models /app && chmod -R a+rX /models /app
USER kokoro
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
