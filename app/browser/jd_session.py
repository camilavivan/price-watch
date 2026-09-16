"""JD Playwright session paths, status, storage_state helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.config import get_config

logger = logging.getLogger(__name__)

# Cookie name hints that suggest a logged-in JD session
_JD_LOGIN_COOKIE_HINTS = ("pin", "thor", "pt_key", "pt_pin", "pwdt_id", "ceshi3.com")


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
    for c in cookies:
        if isinstance(c, dict) and c.get("name"):
            names.append(str(c["name"]))
    return names


def jd_logged_in_hint(state: Optional[dict] = None) -> bool:
    state = state if state is not None else load_storage_state()
    names = {n.lower() for n in cookie_names_from_state(state)}
    if not names:
        return False
    return any(h.lower() in names for h in _JD_LOGIN_COOKIE_HINTS)


def status_dict() -> dict[str, Any]:
    ensure_browser_dirs()
    enabled = playwright_enabled()
    state = load_storage_state()
    names = cookie_names_from_state(state)
    hints = [n for n in names if n.lower() in {h.lower() for h in _JD_LOGIN_COOKIE_HINTS}]
    logged = bool(hints)
    msg = None
    if not enabled:
        msg = "fetch.playwright.enabled=false"
    elif not state:
        msg = "无 storage_state；请 Web「浏览器登录」或 python -m app.browser_login jd"
    elif not logged:
        msg = "有 storage_state 但未见 pin/thor 等登录 Cookie，可能未登录成功"
    else:
        msg = "已检测到疑似登录 Cookie；自动取价仍可能被风控"
    return {
        "playwright_enabled": enabled,
        "has_storage_state": state is not None,
        "cookie_names": hints or names[:12],
        "jd_logged_in_hint": logged,
        "storage_path": str(storage_state_path()),
        "message": msg,
    }
