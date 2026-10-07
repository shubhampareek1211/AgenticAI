"""FastAPI chat API with PostgreSQL-backed conversations and complete tool history."""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from cricket.audio import AudioError
from cricket.auth import AuthenticationError, AuthSettings, FirebaseAuthorizer
from cricket.chart_view import normalize_chart_spec
from cricket.conversations import (
    ConversationRepository,
    SessionBusy,
    SessionForbidden,
    SessionNotFound,
    locked_session,
    transcript_session,
)
from cricket.db import ConfigurationError, make_engine
from cricket.harness import AgentHarness, utf8_text
from cricket.models import ChartSpec, Message
from cricket.settings import HarnessSettings
from cricket.speech import SpeechError, SpeechService, SpeechSettings
from cricket.tts import TTSError, TTSService, TTSSettings
from cricket.voice_quota import VoiceQuotaExceeded, VoiceQuotaUnavailable, reserve_voice_attempt


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: uuid.UUID | None = None

    @field_validator("message")
    @classmethod
    def nonempty_message(cls, value):
        if not value.strip() or not utf8_text(value):
            raise ValueError("Message must contain text.")
        return value


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


def create_app(
    engine=None,
    harness=None,
    *,
    frontend_dist: Path | None = None,
    speech_settings: SpeechSettings | None = None,
    speech_service: SpeechService | None = None,
    auth_settings: AuthSettings | None = None,
    authorizer: FirebaseAuthorizer | None = None,
    tts_service: TTSService | None = None,
    private_data_endpoint: bool | None = None,
) -> FastAPI:
    """Allow app/process instances to share a DB without sharing Python session state."""

    dist = (frontend_dist or Path(__file__).parent / "frontend" / "dist").resolve()
    private_data_endpoint = (
        os.environ.get("DATA_ENDPOINT_MODE") == "private"
        if private_data_endpoint is None
        else private_data_endpoint
    )

    @asynccontextmanager
    async def lifespan(application):
        owned = engine is None
        application.state.engine = engine
        application.state.configuration_error = None
        try:
            application.state.harness = harness or AgentHarness(HarnessSettings.from_env())
            application.state.engine = engine if engine is not None else make_engine()
            if private_data_endpoint and engine is None:
                try:
                    with application.state.engine.connect() as connection:
                        connection.execute(text("SELECT 1"))
                except BaseException:
                    application.state.engine.dispose()
                    raise
        except ConfigurationError as exc:
            if private_data_endpoint:
                raise
            application.state.configuration_error = str(exc)
        try:
            yield
        finally:
            if owned and application.state.engine is not None:
                application.state.engine.dispose()

    application = FastAPI(lifespan=lifespan)
    application.state.auth_settings = auth_settings or AuthSettings.from_env()
    if private_data_endpoint and not application.state.auth_settings.enabled:
        raise ConfigurationError("The private data endpoint requires APP_ENV=pilot or production.")
    application.state.authorizer = authorizer or FirebaseAuthorizer(application.state.auth_settings)
    application.state.speech = speech_service or SpeechService(
        speech_settings or SpeechSettings.from_env()
    )
    application.state.tts = tts_service or TTSService(TTSSettings.from_env())

    @application.middleware("http")
    async def authorize_api(request: Request, call_next):
        path = request.url.path
        public = path in {
            "/",
            "/auth/finish",
            "/health",
            "/healthz",
            "/auth/config",
            "/assets/plotly-basic-4.1.1.min.js",
        }
        if public or path.startswith("/assets/"):
            return await call_next(request)
        try:
            # On the private Cloud Run backend, Authorization carries its IAM
            # service token. The public gateway forwards the user's Firebase
            # bearer only in this separate header; Cloud Run IAM prevents
            # browsers from reaching the backend directly.
            authorization = request.headers.get(
                "x-firebase-authorization" if private_data_endpoint else "authorization"
            )
            request.state.owner_id = await asyncio.to_thread(
                request.app.state.authorizer.authorize, authorization
            )
        except AuthenticationError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        if request.url.path == "/transcribe":
            return voice_error("invalid_audio", "Invalid recording request.", 422)
        return JSONResponse(
            status_code=422,
            content={
                "detail": (
                    "Provide a nonempty message of at most 8000 characters and a valid session UUID."
                )
            },
        )

    def voice_error(
        code: str,
        detail: str,
        status: int,
        request_id: str | None = None,
        retry_after: int | None = None,
    ):
        headers = {"Retry-After": str(retry_after or 1)} if status == 429 else None
        return JSONResponse(
            status_code=status,
            content={"detail": detail, "code": code, "request_id": request_id or str(uuid.uuid4())},
            headers=headers,
        )

    def database(request):
        if request.app.state.configuration_error:
            raise HTTPException(503, request.app.state.configuration_error)
        return request.app.state.engine

    @application.exception_handler(SessionBusy)
    async def busy_handler(request, exc):
        return JSONResponse(
            status_code=409, content={"detail": str(exc)}, headers={"Retry-After": "1"}
        )

    @application.exception_handler(SessionNotFound)
    async def missing_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(SessionForbidden)
    async def forbidden_handler(request, exc):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @application.exception_handler(SQLAlchemyError)
    async def database_handler(request, exc):
        # Database/driver exceptions can include SQL and connection details.
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Conversation storage is unavailable. Check DATABASE_URL and apply migrations, then retry."
                )
            },
        )

    @application.get("/")
    @application.get("/auth/finish")
    def index():
        built_index = dist / "index.html"
        if not built_index.is_file():
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Frontend build is missing. Run `npm ci` and `npm run build` in frontend/."
                },
            )
        return FileResponse(built_index, headers={"Cache-Control": "no-cache"})

    @application.get("/assets/plotly-basic-4.1.1.min.js")
    def plotly_bundle():
        return FileResponse(
            Path(__file__).parent / "static/vendor/plotly-basic-4.1.1.min.js",
            media_type="text/javascript",
        )

    @application.get("/assets/{asset_path:path}")
    def frontend_asset(asset_path: str):
        """Serve only files emitted beneath the Vite asset directory."""
        asset_root = (dist / "assets").resolve()
        asset = (asset_root / asset_path).resolve()
        if not asset.is_relative_to(asset_root) or not asset.is_file():
            raise HTTPException(404, "Frontend asset not found.")
        return FileResponse(asset, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @application.get("/health")
    @application.get("/healthz")
    def health(request: Request):
        configured = request.app.state.configuration_error is None
        return JSONResponse(
            status_code=200 if configured else 503,
            content={"status": "ok" if configured else "unconfigured"},
        )

    @application.get("/auth/config")
    def auth_config(request: Request):
        return request.app.state.auth_settings.public_config()

    @application.get("/voice/config")
    def voice_config(request: Request):
        return request.app.state.speech.settings.public_config()

    @application.get("/tts/config")
    def tts_config(request: Request):
        return request.app.state.tts.settings.public_config()

    @application.post("/sessions/{session_id}/messages/{message_id}/speech")
    async def synthesize_answer(session_id: uuid.UUID, message_id: uuid.UUID, request: Request):
        """Speak only a saved assistant answer owned by the signed-in user."""
        service = request.app.state.tts
        if not service.settings.enabled:
            return JSONResponse(
                status_code=503, content={"detail": "Generated speech is unavailable."}
            )
        with transcript_session(database(request), session_id) as (session, _):
            ConversationRepository(session, session_id, request.state.owner_id).require()
            message = session.scalar(
                select(Message).where(
                    Message.id == message_id,
                    Message.conversation_id == session_id,
                    Message.role == "assistant",
                )
            )
            if message is None or not isinstance(message.payload.get("content"), str):
                raise HTTPException(404, "Assistant answer not found in this conversation.")
            markdown = message.payload["content"]
        try:
            service.prepare_text(markdown)
        except TTSError as exc:
            return JSONResponse(
                status_code=exc.status, content={"detail": exc.detail, "code": exc.code}
            )
        if request.app.state.auth_settings.enabled:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        reserve_voice_attempt, database(request), request.state.owner_id
                    ),
                    timeout=5,
                )
            except VoiceQuotaExceeded as exc:
                return JSONResponse(
                    status_code=exc.status,
                    content={"detail": exc.detail, "code": exc.code},
                    headers={"Retry-After": str(exc.retry_after_seconds)},
                )
            except (VoiceQuotaUnavailable, asyncio.TimeoutError):
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Voice admission is temporarily unavailable."},
                )
        try:
            frames = await service.stream(markdown)
        except TTSError as exc:
            headers = {"Retry-After": "1"} if exc.status == 429 else None
            return JSONResponse(
                status_code=exc.status,
                content={"detail": exc.detail, "code": exc.code},
                headers=headers,
            )
        return StreamingResponse(
            frames,
            media_type="application/x-cricket-speech-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @application.post("/transcribe")
    async def transcribe(request: Request):
        request_id = str(uuid.uuid4())
        service = request.app.state.speech
        settings = service.settings
        if not settings.enabled:
            return voice_error("voice_unavailable", "Voice input is unavailable.", 503, request_id)
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            return voice_error("invalid_audio", "Use multipart form audio upload.", 422, request_id)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if length > settings.max_body_bytes:
            return voice_error("audio_too_large", "The recording is too large.", 413, request_id)

        async def read_body():
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > settings.max_body_bytes:
                    raise AudioError("audio_too_large", "The recording is too large.", 413)
            return bytes(body)

        async def process_body(body):
            async def body_stream():
                yield body

            parser = MultiPartParser(
                headers=request.headers,
                stream=body_stream(),
                max_files=1,
                max_fields=1,
                max_part_size=settings.max_upload_bytes,
            )
            form = await parser.parse()
            try:
                items = form.multi_items()
                if any(key not in {"file", "language"} for key, _ in items):
                    raise SpeechError("invalid_audio", "Unexpected recording field.", 422)
                files = [value for key, value in items if key == "file"]
                languages = [value for key, value in items if key == "language"]
                if len(files) != 1 or not isinstance(files[0], UploadFile) or len(languages) > 1:
                    raise SpeechError("invalid_audio", "Provide exactly one audio file.", 422)
                language = languages[0] if languages else settings.languages[0]
                if not isinstance(language, str) or language not in settings.languages:
                    raise SpeechError("invalid_language", "Unsupported recording language.", 422)
                upload = files[0]
                data = await upload.read(settings.max_upload_bytes + 1)
                if len(data) > settings.max_upload_bytes:
                    raise SpeechError("audio_too_large", "The recording is too large.", 413)
                text, duration = await service.transcribe_admitted(
                    data, upload.content_type or "", language
                )
            finally:
                await form.close()
            return text, duration, language

        try:
            async with service.admit():
                if request.app.state.auth_settings.enabled:
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(
                                reserve_voice_attempt,
                                database(request),
                                request.state.owner_id,
                            ),
                            timeout=5,
                        )
                    except VoiceQuotaExceeded as exc:
                        return voice_error(
                            exc.code,
                            exc.detail,
                            exc.status,
                            request_id,
                            exc.retry_after_seconds,
                        )
                    except (VoiceQuotaUnavailable, asyncio.TimeoutError):
                        return voice_error(
                            "voice_quota_unavailable",
                            "Voice admission is temporarily unavailable.",
                            503,
                            request_id,
                        )
                try:
                    body = await asyncio.wait_for(read_body(), timeout=30)
                except asyncio.TimeoutError:
                    return voice_error(
                        "transcription_timeout", "Audio upload timed out.", 504, request_id
                    )
                text, duration, language = await asyncio.wait_for(process_body(body), timeout=60)
        except asyncio.TimeoutError:
            return voice_error("transcription_timeout", "Transcription timed out.", 504, request_id)
        except MultiPartException:
            return voice_error("invalid_audio", "Invalid recording upload.", 422, request_id)
        except AudioError as exc:
            return voice_error(exc.code, exc.detail, exc.status, request_id)
        return {
            "text": text,
            "language": language,
            "duration_seconds": duration,
            "request_id": request_id,
        }

    @application.post("/sessions", status_code=201)
    def allocate_session(request: Request):
        """Give the browser a durable UUID before it submits any model work."""
        session_id = uuid.uuid4()
        with locked_session(database(request), session_id) as session:
            ConversationRepository(session, session_id, request.state.owner_id).create(
                request.app.state.harness.system_prompt
            )
        return {"session_id": str(session_id)}

    @application.post("/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest, request: Request):
        session_id = payload.session_id or uuid.uuid4()
        with locked_session(database(request), session_id) as session:
            repo = ConversationRepository(session, session_id, request.state.owner_id)
            if payload.session_id is None:
                repo.create(request.app.state.harness.system_prompt)
            else:
                repo.require()
            response, calls = request.app.state.harness.run(repo, payload.message)
        return ChatResponse(response=response, session_id=str(session_id), tool_calls=calls)

    @application.get("/sessions/{session_id}")
    def transcript(session_id: uuid.UUID, request: Request):
        with transcript_session(database(request), session_id) as (session, running):
            repo = ConversationRepository(session, session_id, request.state.owner_id)
            data = repo.transcript()
            data["request_state"] = (
                "running" if running else "interrupted" if repo.unfinished_turns() else "idle"
            )
            return data

    @application.get("/sessions/{session_id}/charts/{chart_id}")
    def chart(session_id: uuid.UUID, chart_id: uuid.UUID, request: Request):
        """Return a saved figure only through the conversation that owns its dataset."""
        with transcript_session(database(request), session_id) as (session, _):
            ConversationRepository(session, session_id, request.state.owner_id).require()
            saved = session.scalar(
                select(ChartSpec).where(
                    ChartSpec.id == chart_id, ChartSpec.conversation_id == session_id
                )
            )
            if saved is None:
                raise HTTPException(404, "Chart not found in this conversation.")
            return {
                **normalize_chart_spec(saved.spec),
                "chart_id": str(saved.id),
                "dataset_id": str(saved.dataset_id),
            }

    @application.post("/sessions/{session_id}/recover")
    def recover(session_id: uuid.UUID, request: Request):
        """Close abandoned progress under the worker lock without re-executing any tool."""
        with locked_session(database(request), session_id) as session:
            repo = ConversationRepository(session, session_id, request.state.owner_id)
            repo.require()
            repo.recover_interrupted_turns()
        return {"status": "ok"}

    @application.post("/clear")
    def clear(request: Request, session_id: uuid.UUID):
        with locked_session(database(request), session_id) as session:
            ConversationRepository(session, session_id, request.state.owner_id).clear()
        return {"status": "ok"}

    return application


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
