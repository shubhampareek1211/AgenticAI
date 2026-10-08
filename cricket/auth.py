"""Firebase mailbox authentication for the Columbia Cloud Run pilot."""

import os
from collections.abc import Callable
from dataclasses import dataclass

from cricket.db import ConfigurationError


class AuthenticationError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class AuthSettings:
    mode: str = "local"
    project_id: str = ""
    web_api_key: str = ""
    auth_domain: str = ""
    web_app_id: str = ""

    def __post_init__(self):
        if self.mode not in {"local", "test", "pilot", "production"}:
            raise ConfigurationError("APP_ENV must be local, test, pilot, or production.")
        if self.enabled and not all(
            (self.project_id, self.web_api_key, self.auth_domain, self.web_app_id)
        ):
            raise ConfigurationError(
                "Set FIREBASE_PROJECT_ID, FIREBASE_WEB_API_KEY, FIREBASE_AUTH_DOMAIN, "
                "and FIREBASE_WEB_APP_ID for the pilot."
            )

    @property
    def enabled(self) -> bool:
        return self.mode in {"pilot", "production"}

    @classmethod
    def from_env(cls):
        mode = os.environ.get("APP_ENV")
        if os.environ.get("K_SERVICE") and mode not in {"pilot", "production"}:
            raise ConfigurationError("Set APP_ENV=pilot or production on Cloud Run.")
        return cls(
            mode=mode or "local",
            project_id=os.environ.get("FIREBASE_PROJECT_ID", ""),
            web_api_key=os.environ.get("FIREBASE_WEB_API_KEY", ""),
            auth_domain=os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
            web_app_id=os.environ.get("FIREBASE_WEB_APP_ID", ""),
        )

    def public_config(self):
        if not self.enabled:
            return {"enabled": False, "firebaseConfig": None}
        return {
            "enabled": True,
            "firebaseConfig": {
                "apiKey": self.web_api_key,
                "authDomain": self.auth_domain,
                "projectId": self.project_id,
                "appId": self.web_app_id,
            },
        }


class FirebaseAuthorizer:
    """Verify the ID token and constrain access to verified Columbia mailboxes."""

    def __init__(
        self,
        settings: AuthSettings,
        *,
        verify_claims: Callable[[str], dict] | None = None,
    ):
        self.settings = settings
        self._verify_claims = verify_claims
        if settings.enabled and verify_claims is None:
            try:
                import firebase_admin
                from firebase_admin import auth
            except ImportError as exc:
                raise ConfigurationError(
                    "Install firebase-admin for pilot authentication."
                ) from exc

            name = f"cricket-{settings.project_id}"
            try:
                firebase_app = firebase_admin.get_app(name)
            except ValueError:
                firebase_app = firebase_admin.initialize_app(
                    options={"projectId": settings.project_id}, name=name
                )

            def verify(token: str) -> dict:
                try:
                    return auth.verify_id_token(token, app=firebase_app, check_revoked=True)
                except (
                    auth.ExpiredIdTokenError,
                    auth.InvalidIdTokenError,
                    auth.RevokedIdTokenError,
                    auth.UserDisabledError,
                    auth.UserNotFoundError,
                    ValueError,
                ) as exc:
                    raise AuthenticationError(401, "Invalid or expired sign-in token.") from exc
                except Exception as exc:
                    raise AuthenticationError(503, "Sign-in verification is unavailable.") from exc

            self._verify_claims = verify

    def authorize(self, authorization: str | None) -> str | None:
        if not self.settings.enabled:
            return None
        scheme, separator, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not separator or not token or " " in token:
            raise AuthenticationError(401, "Sign in to continue.")
        assert self._verify_claims is not None
        try:
            claims = self._verify_claims(token)
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError(401, "Invalid or expired sign-in token.") from exc
        if (
            not isinstance(claims, dict)
            or claims.get("aud") != self.settings.project_id
            or claims.get("iss") != f"https://securetoken.google.com/{self.settings.project_id}"
        ):
            raise AuthenticationError(401, "Invalid or expired sign-in token.")
        uid = claims.get("uid") or claims.get("sub")
        if not isinstance(uid, str) or not uid or len(uid) > 128:
            raise AuthenticationError(401, "Invalid or expired sign-in token.")
        email = claims.get("email")
        if not isinstance(email, str) or claims.get("email_verified") is not True:
            raise AuthenticationError(403, "A verified Columbia email is required.")
        local, separator, domain = email.rpartition("@")
        if not separator or not local or "@" in local or domain.casefold() != "columbia.edu":
            raise AuthenticationError(403, "A verified Columbia email is required.")
        return f"firebase:{self.settings.project_id}:{uid}"
