from __future__ import annotations

from app.auth import (
    AuthUser,
    create_session_token,
    hash_password,
    parse_session_token,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    stored = hash_password("a very strong password")

    assert verify_password("a very strong password", stored)
    assert not verify_password("wrong password", stored)


def test_session_token_roundtrip() -> None:
    token = create_session_token(42)

    assert parse_session_token(token) == 42
    assert parse_session_token(token + "tampered") is None


def test_role_hierarchy() -> None:
    admin = AuthUser(id=1, email="admin@example.com", display_name="Admin", role="admin")
    viewer = AuthUser(id=2, email="viewer@example.com", display_name="Viewer", role="viewer")

    assert admin.has_role("analyst")
    assert not viewer.has_role("analyst")
