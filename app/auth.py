"""Authentication and role checks for the dashboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.db.models import User
from app.db.session import session_scope
from app.settings import get_settings

ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}
VALID_ROLES = tuple(ROLE_RANK)


@dataclass(frozen=True)
class AuthUser:
    id: int
    email: str
    display_name: str
    role: str

    def has_role(self, minimum_role: str) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK[minimum_role]


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    iterations = 260_000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${digest_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _sign(payload: str) -> str:
    secret = get_settings().auth_secret_key.encode("utf-8")
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_session_token(user_id: int) -> str:
    issued_at = int(time.time())
    payload = f"{user_id}.{issued_at}"
    return f"{payload}.{_sign(payload)}"


def parse_session_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        user_id_text, issued_at_text, signature = token.split(".", 2)
        issued_at = int(issued_at_text)
    except ValueError:
        return None

    payload = f"{user_id_text}.{issued_at_text}"
    if not hmac.compare_digest(_sign(payload), signature):
        return None

    if time.time() - issued_at > get_settings().auth_session_max_age_seconds:
        return None

    try:
        return int(user_id_text)
    except ValueError:
        return None


def set_session_cookie(response: Response, user_id: int) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.auth_session_cookie,
        create_session_token(user_id),
        max_age=settings.auth_session_max_age_seconds,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().auth_session_cookie)


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    result = await session.execute(
        select(User).where(User.id == user_id, User.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def user_count(session: AsyncSession) -> int:
    count = await session.scalar(select(func.count(User.id)))
    return int(count or 0)


async def ensure_auth_schema() -> None:
    async with session_scope() as session:
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role VARCHAR(20) NOT NULL DEFAULT 'viewer',
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_login_at TIMESTAMPTZ NULL,
                    CONSTRAINT ck_users_role CHECK (role in ('viewer', 'analyst', 'admin'))
                )
                """
            )
        )
        await session.execute(
            text("CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)")
        )


def current_user_from_request(request: Request) -> AuthUser | None:
    user = getattr(request.state, "current_user", None)
    return user if isinstance(user, AuthUser) else None


def require_role(minimum_role: str) -> Callable[[Request], AuthUser]:
    def dependency(request: Request) -> AuthUser:
        user = current_user_from_request(request)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if not user.has_role(minimum_role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return dependency


class AuthMiddleware(BaseHTTPMiddleware):
    """Load the signed session cookie and enforce page/API access."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.public_prefixes = ("/static",)
        self.public_paths = {"/", "/health", "/login", "/logout", "/setup", "/api/calendar"}
        self.admin_prefixes = ("/api/admin", "/users", "/bank-research/admin", "/event-log")
        self.analyst_write_prefixes = (
            "/api/cb/analysis",
            "/api/news/sentiment",
            "/bank-research/refresh",
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        request.state.current_user = None

        if not settings.auth_enabled or self._is_public(request.url.path):
            return await call_next(request)

        async with session_scope() as session:
            total_users = await user_count(session)
            if total_users == 0:
                return self._redirect_or_json(request, "/setup")

            user_id = parse_session_token(
                request.cookies.get(settings.auth_session_cookie)
            )
            db_user = await get_user_by_id(session, user_id) if user_id else None
            if db_user is not None:
                request.state.current_user = AuthUser(
                    id=db_user.id,
                    email=db_user.email,
                    display_name=db_user.display_name,
                    role=db_user.role,
                )

        user = current_user_from_request(request)
        if user is None:
            return self._redirect_or_json(request, "/login")

        if self._requires_admin(request.url.path) and not user.has_role("admin"):
            return self._forbidden(request)

        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and self._requires_analyst(request.url.path)
            and not user.has_role("analyst")
        ):
            return self._forbidden(request)

        return await call_next(request)

    def _is_public(self, path: str) -> bool:
        return path in self.public_paths or any(
            path.startswith(prefix) for prefix in self.public_prefixes
        )

    def _requires_admin(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.admin_prefixes)

    def _requires_analyst(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.analyst_write_prefixes)

    def _wants_json(self, request: Request) -> bool:
        return request.url.path.startswith("/api")

    def _redirect_or_json(self, request: Request, path: str) -> Response:
        if self._wants_json(request):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse(f"{path}?next={request.url.path}", status_code=303)

    def _forbidden(self, request: Request) -> Response:
        if self._wants_json(request):
            return JSONResponse({"detail": "Insufficient role"}, status_code=403)
        return Response("Insufficient role", status_code=403)


def mark_login(user: User) -> None:
    user.last_login_at = datetime.now(UTC)
