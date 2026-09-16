"""Load config from config.yaml + environment overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class SchedulerConfig(BaseModel):
    defaultIntervalMinutes: int = 60
    startupDelaySeconds: int = 10
    # When needs_manual + risk-block last_error, skip auto fetch for this many hours
    manualRiskBackoffHours: float = 12


class PlaywrightConfig(BaseModel):
    """Optional Chromium fetch for JD risk/SPA pages (requires playwright install)."""
    enabled: bool = False
    headless: bool = True
    storageStatePath: str = "./data/browser/jd_storage.json"
    userDataDir: str = "./data/browser/profile"
    loginScreenshotPath: str = "./data/browser/login.png"
    timeoutMs: int = 45000
    # Navigate candidates for jd.hk / haitao
    preferMobile: bool = True


class FetchConfig(BaseModel):
    """Outbound fetch politeness (adapter rate limit between requests)."""
    rateLimitSeconds: float = 1.5
    # Ignore drop alerts when relative change is within this noise band (MarketEye-inspired)
    priceNoisePercent: float = 0.5
    playwright: PlaywrightConfig = Field(default_factory=PlaywrightConfig)


class AlertsConfig(BaseModel):
    onBelowTarget: bool = True
    dropPercent: float = 5.0
    dropYuan: float = 0.0
    # First-party local history (self-collected price_history)
    onHistoryLow: bool = True
    historyLowDays: int = 90  # lookback window: 30 / 90 / 180 typical
    historyLowTolerancePercent: float = 0.5  # within 0.5% of window low → 历史新低


class HistoryConfig(BaseModel):
    """External history (慢慢买) + local fallback."""
    enabled: bool = True
    # manmanbuy | none
    external: str = "manmanbuy"
    cacheSeconds: int = 3600
    rateLimitSeconds: float = 2.0
    # When JD/TB auto fetch fails, try latest manmanbuy point as soft hint
    softPriceHint: bool = True
    softPriceHintPlatforms: list[str] = Field(default_factory=lambda: ["jd", "taobao"])


class WebConfig(BaseModel):
    """Debug/admin UI — listen all interfaces; protect with ADMIN_TOKEN when exposed."""
    host: str = "0.0.0.0"
    port: int = 8080


class QQOfficialConfig(BaseModel):
    """QQ 官方开放平台（由同容器内 Node bot 消费；此处供文档与可选回写）。"""
    enabled: bool = True
    appId: str = ""
    secret: str = ""
    sandbox: bool = False
    mode: str = "websocket"  # websocket only for no public port
    sendImages: bool = True
    # Who can chat is gated in QQ Open Platform console (personal developer).
    # App-level allowUsers/allowAll removed — always accept messages that reach the bot.
    # Isolation remains via owner_openid on each watch.
    # Internal URL: app → bot notify (same container → localhost)
    notifyUrl: str = "http://127.0.0.1:8091/notify"


class OneBotConfig(BaseModel):
    """可选 / 已降级：默认关闭。主路径为 qqofficial。"""
    enabled: bool = False
    apiBase: str = "http://host.docker.internal:5700"
    accessToken: str = ""
    notifyGroups: list[int] = Field(default_factory=list)
    notifyUsers: list[int] = Field(default_factory=list)


class AppConfig(BaseModel):
    adminToken: str = ""
    databaseUrl: str = "sqlite+aiosqlite:///./data/pricewatch.db"
    web: WebConfig = Field(default_factory=WebConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    qqofficial: QQOfficialConfig = Field(default_factory=QQOfficialConfig)
    onebot: OneBotConfig = Field(default_factory=OneBotConfig)
    # 不做微信/企微产品路径；保留字段仅为兼容旧 config.yaml（始终视为关闭）
    wecom: dict[str, Any] = Field(default_factory=dict)


_CONFIG: AppConfig | None = None


def _find_config_path() -> Path | None:
    for candidate in (
        Path(os.environ.get("CONFIG_PATH", "")),
        Path("config.yaml"),
        Path("/app/config.yaml"),
        Path("config.example.yaml"),
    ):
        if candidate and candidate.is_file():
            return candidate
    return None


def load_config(force: bool = False) -> AppConfig:
    global _CONFIG
    if _CONFIG is not None and not force:
        return _CONFIG

    data: dict[str, Any] = {}
    path = _find_config_path()
    if path:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
            if isinstance(raw, dict):
                data = raw

    # Ignore legacy wecom / smzdm openapi keys if present in old configs
    data.pop("wecom", None)
    data.pop("smzdm", None)

    cfg = AppConfig.model_validate(data)

    if os.environ.get("ADMIN_TOKEN"):
        cfg.adminToken = os.environ["ADMIN_TOKEN"]
    if os.environ.get("DATABASE_URL"):
        cfg.databaseUrl = os.environ["DATABASE_URL"]
    if os.environ.get("ONEBOT_ACCESS_TOKEN"):
        cfg.onebot.accessToken = os.environ["ONEBOT_ACCESS_TOKEN"]
    if os.environ.get("ONEBOT_API_BASE"):
        cfg.onebot.apiBase = os.environ["ONEBOT_API_BASE"]
    if os.environ.get("QQ_BOT_APP_ID"):
        cfg.qqofficial.appId = os.environ["QQ_BOT_APP_ID"]
    if os.environ.get("QQ_BOT_SECRET"):
        cfg.qqofficial.secret = os.environ["QQ_BOT_SECRET"]
    if os.environ.get("BOT_NOTIFY_URL"):
        cfg.qqofficial.notifyUrl = os.environ["BOT_NOTIFY_URL"]
    send_img = os.environ.get("QQ_BOT_SEND_IMAGES")
    if send_img is not None:
        cfg.qqofficial.sendImages = send_img.strip().lower() in ("1", "true", "yes", "on")

    _CONFIG = cfg
    return cfg


def get_config() -> AppConfig:
    return load_config()
