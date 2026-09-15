"""Simple ADMIN_TOKEN cookie/header auth."""

from __future__ import annotations

from fastapi import HTTPException, Request, Response

from app.config import get_config

COOKIE_NAME = "admin_token"
HEADER_NAME = "X-Admin-Token"


def auth_required() -> bool:
    token = (get_config().adminToken or "").strip()
    return bool(token)


def check_auth(request: Request) -> bool:
    expected = (get_config().adminToken or "").strip()
    if not expected:
        return True
    header = request.headers.get(HEADER_NAME, "")
    cookie = request.cookies.get(COOKIE_NAME, "")
    return header == expected or cookie == expected


def require_auth(request: Request) -> None:
    if not check_auth(request):
        raise HTTPException(status_code=401, detail="未授权：请设置 Cookie 或 Header X-Admin-Token")


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
