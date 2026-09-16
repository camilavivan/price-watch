"""JD session paths, status, storage_state helpers (cookie paste + optional Playwright)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.config import get_config

logger = logging.getLogger(__name__)

# Cookie name hints that suggest a logged-in JD session (Product-Crawling style)
_JD_LOGIN_COOKIE_HINTS = ("thor", "pin", "pinId", "pt_key", "pt_pin", "pwdt_id", "ceshi3.com")


def _pw_cfg():
    return getattr(get_config().fetch, "playwright", None)


def playwright_enabled() -> bool:
    cfg = _pw_cfg()
    return bool(cfg and getattr(cfg, "enabled", False))


def storage_state_path() -> Path:
    cfg = _pw_cfg()
    raw = (
        getattr(cfg, "storageStatePath", None) if cfg else None
    ) or "./data/browser/jd_storage.json"
    return Path(raw)


def cookie_txt_path() -> Path:
    return storage_state_path().parent / "jd_cookie.txt"


def user_data_dir() -> Path:
    cfg = _pw_cfg()
    raw = (getattr(cfg, "userDataDir", None) if cfg else None) or "./data/browser/profile"
    return Path(raw)


def login_screenshot_path() -> Path:
    cfg = _pw_cfg()
    raw = (
        getattr(cfg, "loginScreenshotPath", None) if cfg else None
    ) or "./data/browser/login.png"
    return Path(raw)


def ensure_browser_dirs() -> None:
    storage_state_path().parent.mkdir(parents=True, exist_ok=True)
    user_data_dir().mkdir(parents=True, exist_ok=True)
    login_screenshot_path().parent.mkdir(parents=True, exist_ok=True)


def load_storage_state() -> Optional[dict[str, Any]]:
    path = storage_state_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("load storage_state failed: %s", e)
        return None


def cookie_names_from_state(state: Optional[dict]) -> list[str]:
    if not state:
        return []
    cookies = state.get("cookies") or []
    names = []
    seen = set()
    for c in cookies:
        if isinstance(c, dict) and c.get("name"):
            n = str(c["name"])
            if n not in seen:
                seen.add(n)
                names.append(n)
    return names


def jd_logged_in_hint(state: Optional[dict] = None) -> bool:
    """Detect login via storage_state and/or jd_cookie.txt markers."""
    try:
        from app.browser.jd_cookies import load_cookie_dict, looks_logged_in

        cookies = load_cookie_dict()
        if looks_logged_in(cookies):
            return True
    except Exception:
        pass
    state = state if state is not None else load_storage_state()
    names = {n.lower() for n in cookie_names_from_state(state)}
    if not names:
        return False
    return any(h.lower() in names for h in _JD_LOGIN_COOKIE_HINTS)


def has_usable_cookies() -> bool:
    """True when we have cookie material for HTTP fetch (independent of Playwright)."""
    return jd_logged_in_hint()


def status_dict() -> dict[str, Any]:
    ensure_browser_dirs()
    enabled = playwright_enabled()
    state = load_storage_state()
    names = cookie_names_from_state(state)
    try:
        from app.browser.jd_cookies import (
            cookie_file_path,
            detect_login_markers,
            load_cookie_dict,
        )

        cookie_dict = load_cookie_dict()
        markers = detect_login_markers(cookie_dict) or [
            n for n in names if n.lower() in {h.lower() for h in _JD_LOGIN_COOKIE_HINTS}
        ]
        has_cookie_file = cookie_file_path().is_file()
    except Exception:
        cookie_dict = {}
        markers = [
            n for n in names if n.lower() in {h.lower() for h in _JD_LOGIN_COOKIE_HINTS}
        ]
        has_cookie_file = False

    logged = bool(markers) or jd_logged_in_hint(state)
    cookie_http_ready = logged

    if logged:
        msg = (
            "已检测到登录 Cookie（thor/pin/pinId 等）；HTTP 带 Cookie 取价可用。"
            "云主机扫码常失败，请优先用「粘贴 Cookie」。"
        )
    elif state or has_cookie_file:
        msg = "有 Cookie 文件但未见 thor/pin/pinId，可能未登录成功；请重新粘贴"
    else:
        msg = (
            "无 Cookie。请在本机浏览器登录 jd.com 后复制 Cookie，"
            "粘贴到本页「保存 Cookie」（云主机扫码常出现「当前页面异常」）"
        )

    return {
        "playwright_enabled": enabled,
        "has_storage_state": state is not None,
        "has_cookie_file": has_cookie_file,
        "cookie_http_ready": cookie_http_ready,
        "cookie_names": markers or names[:12],
        "jd_logged_in_hint": logged,
        "storage_path": str(storage_state_path()),
        "cookie_path": str(cookie_txt_path()),
        "cookie_count": len(cookie_dict) if cookie_dict else len(names),
        "message": msg,
        # Prefer cookie paste over server QR
        "preferred_login": "paste_cookie",
        "qr_warning": "云主机扫码登录常失败（当前页面异常），请改用粘贴 Cookie",
    }
