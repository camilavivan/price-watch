"""JD cookie paste helpers — thin wrappers over cookies_common (backward compat)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.browser.cookies_common import (
    cookie_dict_to_storage_state as _dict_to_state,
    cookie_path_for,
    cookies_to_header,
    detect_login_markers as _detect,
    load_cookie_dict as _load_dict,
    load_cookie_header as _load_header,
    looks_logged_in as _looks,
    parse_cookie_header,
    save_cookie_bundle as _save,
    storage_state_to_cookie_dict,
)

# Re-export Product-Crawling style markers
JD_LOGIN_MARKERS = ("thor", "pin", "pinId", "pt_key", "pt_pin", "pwdt_id")


def cookie_file_path() -> Path:
    return cookie_path_for("jd")


def detect_login_markers(cookies: dict[str, str] | None) -> list[str]:
    return _detect(cookies, "jd")


def looks_logged_in(cookies: dict[str, str] | None) -> bool:
    return _looks(cookies, "jd")


def cookie_dict_to_storage_state(
    cookies: dict[str, str],
    *,
    domains: tuple[str, ...] = (".jd.com", ".jd.hk"),
) -> dict[str, Any]:
    # domains arg kept for API compat; platform spec domains used
    _ = domains
    return _dict_to_state(cookies, "jd")


def save_cookie_bundle(
    raw_header: str,
    *,
    storage_path: Optional[Path] = None,
    cookie_path: Optional[Path] = None,
) -> dict[str, Any]:
    _ = storage_path, cookie_path  # paths come from cookies_common / jd_session
    return _save("jd", raw_header)


def load_cookie_header() -> Optional[str]:
    return _load_header("jd")


def load_cookie_dict() -> dict[str, str]:
    return _load_dict("jd")


__all__ = [
    "JD_LOGIN_MARKERS",
    "cookie_file_path",
    "parse_cookie_header",
    "cookies_to_header",
    "detect_login_markers",
    "looks_logged_in",
    "cookie_dict_to_storage_state",
    "storage_state_to_cookie_dict",
    "save_cookie_bundle",
    "load_cookie_header",
    "load_cookie_dict",
]
