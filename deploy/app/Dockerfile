# Build from the repository root. Base image indexes resolve to the platform
# manifest for each build; release images are linux/amd64.
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm test && npm run build

FROM python:3.13-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed AS app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg && rm -rf /var/lib/apt/lists/* && \
    groupadd --system cricket && useradd --system --gid cricket --home-dir /app cricket
RUN python -m pip install --no-cache-dir uv==0.11.19
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN UV_PROJECT_ENVIRONMENT=/opt/venv uv sync --frozen --no-dev --no-install-project
COPY app.py tools.py alembic.ini ./
COPY cricket/ ./cricket/
COPY migrations/ ./migrations/
COPY static/ ./static/
COPY --from=frontend-build /src/frontend/dist/ ./frontend/dist/
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER cricket
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
