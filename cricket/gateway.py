"""Public Firebase login shell and IAM-authenticated proxy to the private data API.

This process never imports a database driver or stores conversations. Cloud Run
IAM admits only the gateway service account to the private backend. That backend
verifies the forwarded Firebase ID token for every application request.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

MAX_PROXY_BODY = 10 * 1024 * 1024
# A five-round chat can make several 60-second model calls. Cloud Run's planned
# 300-second request limit is the outer bound; HTTPX's read value is per read.
PROXY_TIMEOUT = httpx.Timeout(connect=5, read=285, write=35, pool=5)
RESPONSE_HEADERS = ("content-type", "cache-control", "retry-after", "x-content-type-options")


@dataclass(frozen=True)
class GatewaySettings:
    backend_url: str
    audience: str
    firebase_project_id: str
    firebase_web_api_key: str
    firebase_auth_domain: str
    firebase_web_app_id: str

    def __post_init__(self):
        parsed = urlsplit(self.backend_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("BACKEND_BASE_URL must be an HTTP service origin.")
        if self.audience != self.backend_url.rstrip("/"):
            raise ValueError("BACKEND_AUDIENCE must equal BACKEND_BASE_URL.")
        if os.getenv("K_SERVICE") and parsed.scheme != "https":
            raise ValueError("Cloud Run backend must use HTTPS.")
        if not all(
            (
                self.firebase_project_id,
                self.firebase_web_api_key,
                self.firebase_auth_domain,
                self.firebase_web_app_id,
            )
        ):
            raise ValueError("Firebase public web configuration is incomplete.")

    @classmethod
    def from_env(cls):
        if os.getenv("DATABASE_URL"):
            raise ValueError("The public gateway must not receive DATABASE_URL.")
        return cls(
            backend_url=os.getenv("BACKEND_BASE_URL", ""),
            audience=os.getenv("BACKEND_AUDIENCE", ""),
            firebase_project_id=os.getenv("FIREBASE_PROJECT_ID", ""),
            firebase_web_api_key=os.getenv("FIREBASE_WEB_API_KEY", ""),
            firebase_auth_domain=os.getenv("FIREBASE_AUTH_DOMAIN", ""),
            firebase_web_app_id=os.getenv("FIREBASE_WEB_APP_ID", ""),
        )

    def public_auth_config(self) -> dict:
        return {
            "enabled": True,
            "firebaseConfig": {
                "apiKey": self.firebase_web_api_key,
                "authDomain": self.firebase_auth_domain,
                "projectId": self.firebase_project_id,
                "appId": self.firebase_web_app_id,
            },
        }


def fetch_backend_token(audience: str) -> str:
    """Mint a short-lived ID token as the gateway service account."""
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.id_token import fetch_id_token

    request = GoogleRequest()

    def bounded_request(url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        return request(
            url,
            method=method,
            body=body,
            headers=headers,
            timeout=min(timeout, 5) if timeout is not None else 5,
            **kwargs,
        )

    return fetch_id_token(bounded_request, audience)


def proxy_path_allowed(path: str, method: str) -> bool:
    if path in {"/chat", "/sessions", "/transcribe", "/clear"}:
        return method == "POST"
    if path in {"/voice/config", "/tts/config"}:
        return method == "GET"
    if not path.startswith("/sessions/"):
        return False
    parts = path.strip("/").split("/")
    if len(parts) == 2:
        return method == "GET"
    if len(parts) == 3 and parts[2] == "recover":
        return method == "POST"
    if len(parts) == 4 and parts[2] == "charts":
        return method == "GET"
    if len(parts) == 5 and parts[2] == "messages" and parts[4] == "speech":
        return method == "POST"
    return False


def create_app(
    settings: GatewaySettings | None = None,
    *,
    frontend_dist: Path | None = None,
    token_provider=None,
    transport=None,
) -> FastAPI:
    dist = (frontend_dist or Path(__file__).parent.parent / "frontend" / "dist").resolve()
    token_provider = token_provider or fetch_backend_token

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.settings = settings or GatewaySettings.from_env()
        yield

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    voice_gate = asyncio.BoundedSemaphore(1)

    @application.get("/")
    @application.get("/auth/finish")
    def index():
        file = dist / "index.html"
        if not file.is_file():
            raise HTTPException(503, "Frontend build is missing.")
        return FileResponse(file, headers={"Cache-Control": "no-cache"})

    @application.get("/assets/plotly-basic-4.1.1.min.js")
    def plotly_bundle():
        return FileResponse(
            Path(__file__).parent.parent / "static/vendor/plotly-basic-4.1.1.min.js",
            media_type="text/javascript",
        )

    @application.get("/assets/{asset_path:path}")
    def asset(asset_path: str):
        root = (dist / "assets").resolve()
        file = (root / asset_path).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            raise HTTPException(404, "Frontend asset not found.")
        return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @application.get("/auth/config")
    def auth_config(request: Request):
        return JSONResponse(
            request.app.state.settings.public_auth_config(), headers={"Cache-Control": "no-store"}
        )

    @application.get("/healthz")
    def health():
        return {"status": "ok"}

    @application.api_route("/{path:path}", methods=["GET", "POST"])
    async def proxy(request: Request, path: str):
        settings = request.app.state.settings
        backend_path = "/" + path
        if not proxy_path_allowed(backend_path, request.method):
            raise HTTPException(404, "Endpoint not found.")
        # The gateway never trusts a client-supplied X-Firebase-Authorization.
        # Only Authorization is copied into the backend's user-token header.
        firebase_authorization = request.headers.get("authorization", "")
        scheme, separator, firebase_token = firebase_authorization.partition(" ")
        if (
            scheme.lower() != "bearer"
            or not separator
            or not firebase_token
            or " " in firebase_token
        ):
            raise HTTPException(401, "Sign in to continue.")
        try:
            service_token = await asyncio.wait_for(
                asyncio.to_thread(token_provider, settings.audience), timeout=5
            )
        except Exception as exc:
            raise HTTPException(503, "Data service authentication is unavailable.") from exc
        if not isinstance(service_token, str) or not service_token:
            raise HTTPException(503, "Data service authentication is unavailable.")

        voice_admitted = backend_path == "/transcribe"
        if voice_admitted:
            try:
                await asyncio.wait_for(voice_gate.acquire(), timeout=0.001)
            except TimeoutError as exc:
                raise HTTPException(
                    429, "Voice upload is busy.", headers={"Retry-After": "1"}
                ) from exc

        async def read_body():
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_PROXY_BODY:
                    raise HTTPException(413, "Request is too large.")
            return bytes(body)

        try:
            try:
                body = await asyncio.wait_for(read_body(), timeout=30)
            except TimeoutError as exc:
                raise HTTPException(408, "Upload timed out.") from exc

            headers = {
                "Authorization": f"Bearer {service_token}",
                "X-Firebase-Authorization": firebase_authorization,
            }
            if content_type := request.headers.get("content-type"):
                headers["Content-Type"] = content_type
            if accept := request.headers.get("accept"):
                headers["Accept"] = accept
            query = request.url.query
            url = settings.backend_url.rstrip("/") + backend_path + (f"?{query}" if query else "")
            client = httpx.AsyncClient(
                transport=transport, timeout=PROXY_TIMEOUT, follow_redirects=False
            )
            try:
                upstream = await client.send(
                    client.build_request(request.method, url, headers=headers, content=body),
                    stream=True,
                )
            except httpx.HTTPError as exc:
                await client.aclose()
                raise HTTPException(503, "Data service is unavailable.") from exc
            except BaseException:
                await client.aclose()
                raise
        except BaseException:
            if voice_admitted:
                voice_gate.release()
            raise

        released = False

        async def close_upstream():
            nonlocal released
            try:
                await upstream.aclose()
            finally:
                try:
                    await client.aclose()
                finally:
                    if voice_admitted and not released:
                        voice_gate.release()
                        released = True

        async def chunks():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await close_upstream()

        response_headers = {
            key: value for key, value in upstream.headers.items() if key.lower() in RESPONSE_HEADERS
        }
        return StreamingResponse(
            chunks(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(close_upstream),
        )

    return application


app = create_app()
