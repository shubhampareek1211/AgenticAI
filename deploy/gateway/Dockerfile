# Public static/login shell. This image has no SQL client and no database URL.
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm test && npm run build

FROM python:3.13-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed
RUN python -m pip install --no-cache-dir fastapi==0.141.1 uvicorn==0.53.0 \
      httpx==0.28.1 google-auth==2.58.0 requests==2.34.2 && \
    groupadd --system cricket && useradd --system --gid cricket --home-dir /app cricket
WORKDIR /app
COPY cricket/__init__.py cricket/gateway.py ./cricket/
COPY static/vendor/plotly-basic-4.1.1.min.js ./static/vendor/plotly-basic-4.1.1.min.js
COPY --from=frontend-build /src/frontend/dist/ ./frontend/dist/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER cricket
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn cricket.gateway:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log"]
