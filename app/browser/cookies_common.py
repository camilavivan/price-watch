"""Shared Cookie-header paste → storage_state + plain cookie file (JD / TB / PDD)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlatformCookieSpec:
    platform: str
    display_name: str
    # Login marker cookie names (case-insensitive match)
    markers: tuple[str, ...]
    # Domains written into Playwright storage_state
    domains: tuple[str, ...]
    storage_filename: str
    cookie_filename: str
    # How-to hint shown on Web
    paste_hint: str


SPECS: dict[str, PlatformCookieSpec] = {
    "jd": PlatformCookieSpec(
        platform="jd",
        display_name="京东",
        markers=("thor", "pin", "pinId", "pt_key", "pt_pin", "pwdt_id"),
        domains=(".jd.com", ".jd.hk"),
        storage_filename="jd_storage.json",
        cookie_filename="jd_cookie.txt",
        paste_hint=(
            "本机浏览器打开 jd.com 并登录 → F12 → Network → 任意请求 → "
            "Request Headers 里复制完整 Cookie → 粘贴保存。"
            "云主机扫码常出现「当前页面异常」，请优先粘贴。"
        ),
    ),
    "taobao": PlatformCookieSpec(
        platform="taobao",
        display_name="淘宝/天猫",
        markers=("unb", "tracknick", "_nk_", "cookie2", "t", "cna", "_m_h5_tk"),
        domains=(".taobao.com", ".tmall.com", ".tmall.hk"),
        storage_filename="taobao_storage.json",
        cookie_filename="taobao_cookie.txt",
        paste_hint=(
            "本机浏览器打开 taobao.com / tmall.com 并登录 → F12 复制 Cookie。"
            "登录线索常见：unb / tracknick / _nk_ / cookie2。"
        ),
    ),
    "pdd": PlatformCookieSpec(
        platform="pdd",
        display_name="拼多多",
        markers=("PDDAccessToken", "pdd_user_id", "pdd_user_uin", "api_uid", "rckk"),
        domains=(".yangkeduo.com", ".pinduoduo.com"),
        storage_filename="pdd_storage.json",
        cookie_filename="pdd_cookie.txt",
        paste_hint=(
            "本机浏览器打开 mobile.yangkeduo.com 并登录 → F12 复制 Cookie。"
            "拼多多风控较严，失败请用 QQ「填价」。"
        ),
    ),
}


def get_spec(platform: str) -> PlatformCookieSpec:
    key = (platform or "").strip().lower()
    if key in ("tmall", "天猫", "淘宝"):
        key = "taobao"
    if key in ("jingdong", "京东"):
        key = "jd"
    if key in ("pinduoduo", "拼多多"):
        key = "pdd"
    if key not in SPECS:
        raise KeyError(f"unknown platform: {platform}")
    return SPECS[key]


def browser_data_dir() -> Path:
    # Keep under data/browser alongside existing jd paths
    try:
        from app.config import get_config

        cfg = getattr(get_config().fetch, "playwright", None)
        raw = (
            getattr(cfg, "storageStatePath", None) if cfg else None
        ) or "./data/browser/jd_storage.json"
        return Path(raw).parent
    except Exception:
        return Path("./data/browser")


def storage_path_for(platform: str) -> Path:
    spec = get_spec(platform)
    # JD keeps configurable path for backward compat
    if spec.platform == "jd":
        try:
            from app.browser.jd_session import storage_state_path

            return storage_state_path()
        except Exception:
            pass
    return browser_data_dir() / spec.storage_filename


def cookie_path_for(platform: str) -> Path:
    spec = get_spec(platform)
    return browser_data_dir() / spec.cookie_filename


def ensure_browser_dirs() -> None:
    browser_data_dir().mkdir(parents=True, exist_ok=True)
    try:
        from app.browser.jd_session import ensure_browser_dirs as _jd_dirs

        _jd_dirs()
    except Exception:
        pass


def parse_cookie_header(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    if not text:
        return {}
    if re.match(r"(?i)^cookie\s*:", text):
        text = text.split(":", 1)[1].strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" in text and ";" not in text.split("\n", 1)[0]:
        parts = []
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts.append(line)
        text = "; ".join(parts)

    out: dict[str, str] = {}
    skip_attrs = {
        "path",
        "domain",
        "expires",
        "max-age",
        "secure",
        "httponly",
        "samesite",
        "priority",
    }
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or name.lower() in skip_attrs:
            continue
        out[name] = value
    return out


def cookies_to_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k)


def detect_login_markers(cookies: dict[str, str] | None, platform: str) -> list[str]:
    if not cookies:
        return []
    spec = get_spec(platform)
    names_lower = {k.lower(): k for k in cookies}
    found: list[str] = []
    for m in spec.markers:
        if m.lower() in names_lower:
            found.append(names_lower[m.lower()])
    return found


def looks_logged_in(cookies: dict[str, str] | None, platform: str) -> bool:
    return bool(detect_login_markers(cookies, platform))


def cookie_dict_to_storage_state(
    cookies: dict[str, str],
    platform: str,
) -> dict[str, Any]:
    spec = get_spec(platform)
    entries: list[dict[str, Any]] = []
    http_only_hints = {"thor", "pt_key", "pwdt_id", "cookie2", "pddaccesstoken"}
    for domain in spec.domains:
        for name, value in cookies.items():
            entries.append(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                    "expires": -1,
                    "httpOnly": name.lower() in http_only_hints,
                    "secure": True,
                    "sameSite": "Lax",
                }
            )
    return {"cookies": entries, "origins": []}


def storage_state_to_cookie_dict(state: Optional[dict[str, Any]]) -> dict[str, str]:
    if not state:
        return {}
    out: dict[str, str] = {}
    for c in state.get("cookies") or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name or name in out:
            continue
        out[str(name)] = str(c.get("value") or "")
    return out


def save_cookie_bundle(platform: str, raw_header: str) -> dict[str, Any]:
    ensure_browser_dirs()
    spec = get_spec(platform)
    cookies = parse_cookie_header(raw_header)
    if not cookies:
        return {
            "ok": False,
            "platform": spec.platform,
            "error": "未能解析 Cookie（请粘贴浏览器请求头里的完整 Cookie 字符串）",
            "markers": [],
            "logged_in": False,
            "cookie_count": 0,
        }

    markers = detect_login_markers(cookies, spec.platform)
    logged = bool(markers)
    header = cookies_to_header(cookies)
    state = cookie_dict_to_storage_state(cookies, spec.platform)

    sp = storage_path_for(spec.platform)
    cp = cookie_path_for(spec.platform)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    cp.write_text(header + "\n", encoding="utf-8")
    logger.info(
        "saved %s cookie bundle markers=%s count=%d",
        spec.platform,
        markers,
        len(cookies),
    )
    return {
        "ok": True,
        "platform": spec.platform,
        "error": None,
        "markers": markers,
        "logged_in": logged,
        "cookie_count": len(cookies),
        "storage_path": str(sp),
        "cookie_path": str(cp),
        "message": (
            f"已保存{spec.display_name} Cookie；登录线索：" + ", ".join(markers)
            if logged
            else f"已保存，但未检测到常见登录线索（{', '.join(spec.markers[:4])}…），可能未登录完整"
        ),
    }


def load_cookie_header(platform: str) -> Optional[str]:
    cp = cookie_path_for(platform)
    if cp.is_file():
        try:
            text = cp.read_text(encoding="utf-8").strip()
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    if parse_cookie_header(line):
                        return line
        except Exception as e:
            logger.warning("read cookie file %s failed: %s", cp, e)

    sp = storage_path_for(platform)
    if sp.is_file():
        try:
            state = json.loads(sp.read_text(encoding="utf-8"))
            cookies = storage_state_to_cookie_dict(state)
            if cookies:
                return cookies_to_header(cookies)
        except Exception as e:
            logger.warning("read storage_state %s failed: %s", sp, e)
    return None


def load_cookie_dict(platform: str) -> dict[str, str]:
    return parse_cookie_header(load_cookie_header(platform) or "")


def platform_status(platform: str) -> dict[str, Any]:
    spec = get_spec(platform)
    ensure_browser_dirs()
    cookies = load_cookie_dict(spec.platform)
    markers = detect_login_markers(cookies, spec.platform)
    logged = bool(markers)
    has_file = cookie_path_for(spec.platform).is_file() or storage_path_for(
        spec.platform
    ).is_file()
    if logged:
        msg = f"{spec.display_name}：已检测到登录线索（{', '.join(markers)}）；HTTP 带 Cookie 取价可用"
    elif has_file:
        msg = f"{spec.display_name}：有 Cookie 文件但未见登录线索，请重新粘贴"
    else:
        msg = f"{spec.display_name}：无 Cookie；请本机登录后粘贴，失败请用「填价」"
    return {
        "platform": spec.platform,
        "display_name": spec.display_name,
        "logged_in": logged,
        "markers": markers,
        "cookie_count": len(cookies),
        "has_cookie_file": cookie_path_for(spec.platform).is_file(),
        "has_storage_state": storage_path_for(spec.platform).is_file(),
        "storage_path": str(storage_path_for(spec.platform)),
        "cookie_path": str(cookie_path_for(spec.platform)),
        "paste_hint": spec.paste_hint,
        "message": msg,
    }


def all_platforms_status() -> dict[str, Any]:
    platforms = [platform_status(p) for p in ("jd", "taobao", "pdd")]
    return {
        "platforms": platforms,
        "any_logged_in": any(p["logged_in"] for p in platforms),
        "preferred_login": "paste_cookie",
        "qr_warning": "云主机扫码登录常失败（当前页面异常），请改用粘贴 Cookie",
    }
