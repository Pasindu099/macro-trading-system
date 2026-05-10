"""Login, first-run setup, and user administration routes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    VALID_ROLES,
    clear_session_cookie,
    get_user_by_email,
    hash_password,
    mark_login,
    normalize_email,
    require_role,
    set_session_cookie,
    user_count,
    verify_password,
)
from app.db.models import User
from app.db.session import get_session
from app.settings import get_settings

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))
SessionDep = Depends(get_session)
AdminDep = Depends(require_role("admin"))


def _form_value(form: dict[str, list[str]], key: str, default: str = "") -> str:
    return (form.get(key) or [default])[0].strip()


def _next_path(request: Request, fallback: str = "/") -> str:
    next_path = request.query_params.get("next") or fallback
    if not next_path.startswith("/") or next_path.startswith("//"):
        return fallback
    return next_path


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "page_title": "Sign in | Macro Dashboard",
            "error": request.query_params.get("error", ""),
            "next_path": _next_path(request),
            "auth_enabled": get_settings().auth_enabled,
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    session: AsyncSession = SessionDep,
) -> Response:
    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    email = normalize_email(_form_value(form, "email"))
    password = _form_value(form, "password")
    next_path = _form_value(form, "next", "/")

    user = await get_user_by_email(session, email)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "page_title": "Sign in | Macro Dashboard",
                "error": "Invalid email or password.",
                "next_path": next_path,
                "auth_enabled": get_settings().auth_enabled,
            },
            status_code=401,
        )

    mark_login(user)
    await session.commit()
    response = RedirectResponse(next_path if next_path.startswith("/") else "/", status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.post("/logout", response_class=HTMLResponse)
async def logout_submit() -> Response:
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(
    request: Request,
    session: AsyncSession = SessionDep,
) -> HTMLResponse:
    if await user_count(session) > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "request": request,
            "page_title": "Create admin | Macro Dashboard",
            "error": "",
        },
    )


@router.post("/setup", response_class=HTMLResponse)
async def setup_submit(
    request: Request,
    session: AsyncSession = SessionDep,
) -> Response:
    if await user_count(session) > 0:
        return RedirectResponse("/login", status_code=303)

    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    email = normalize_email(_form_value(form, "email"))
    display_name = _form_value(form, "display_name") or email
    password = _form_value(form, "password")

    if not email or "@" not in email or len(password) < 10:
        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "request": request,
                "page_title": "Create admin | Macro Dashboard",
                "error": "Use a valid email and a password with at least 10 characters.",
            },
            status_code=400,
        )

    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        role="admin",
        is_active=True,
        last_login_at=datetime.now(UTC),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    _: User = AdminDep,
    session: AsyncSession = SessionDep,
) -> HTMLResponse:
    result = await session.execute(select(User).order_by(User.email))
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "request": request,
            "page_title": "Users | Macro Dashboard",
            "users": result.scalars().all(),
            "roles": VALID_ROLES,
            "message": request.query_params.get("msg", ""),
        },
    )


@router.post("/users", response_class=HTMLResponse)
async def create_user(
    request: Request,
    _: User = AdminDep,
    session: AsyncSession = SessionDep,
) -> Response:
    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    email = normalize_email(_form_value(form, "email"))
    display_name = _form_value(form, "display_name") or email
    password = _form_value(form, "password")
    role = _form_value(form, "role", "viewer")

    if role not in VALID_ROLES or not email or "@" not in email or len(password) < 10:
        return RedirectResponse("/users?msg=invalid", status_code=303)
    if await get_user_by_email(session, email) is not None:
        return RedirectResponse("/users?msg=exists", status_code=303)

    session.add(
        User(
            email=email,
            display_name=display_name,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
    )
    await session.commit()
    return RedirectResponse("/users?msg=created", status_code=303)


@router.post("/users/{user_id}", response_class=HTMLResponse)
async def update_user(
    user_id: int,
    request: Request,
    _: User = AdminDep,
    session: AsyncSession = SessionDep,
) -> Response:
    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return RedirectResponse("/users?msg=missing", status_code=303)

    role = _form_value(form, "role", user.role)
    action = _form_value(form, "action", "save")
    password = _form_value(form, "password")

    if role not in VALID_ROLES:
        return RedirectResponse("/users?msg=invalid", status_code=303)

    user.display_name = _form_value(form, "display_name", user.display_name) or user.email
    user.role = role
    user.is_active = action != "deactivate"
    user.updated_at = datetime.now(UTC)
    if password:
        if len(password) < 10:
            return RedirectResponse("/users?msg=short_password", status_code=303)
        user.password_hash = hash_password(password)

    await session.commit()
    return RedirectResponse("/users?msg=updated", status_code=303)
