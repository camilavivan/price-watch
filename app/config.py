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


class AlertsConfig(BaseModel):
    onBelowTarget: bool = True
    dropPercent: float = 5.0
    dropYuan: float = 0.0


class OneBotConfig(BaseModel):
    enabled: bool = False
    apiBase: str = "http://host.docker.internal:5700"
    accessToken: str = ""
    notifyGroups: list[int] = Field(default_factory=list)
    notifyUsers: list[int] = Field(default_factory=list)


class WecomConfig(BaseModel):
    enabled: bool = False
    webhookUrl: str = ""


class AppConfig(BaseModel):
    adminToken: str = ""
    databaseUrl: str = "sqlite+aiosqlite:///./data/pricewatch.db"
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    onebot: OneBotConfig = Field(default_factory=OneBotConfig)
    wecom: WecomConfig = Field(default_factory=WecomConfig)


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

    cfg = AppConfig.model_validate(data)

    # Environment overrides (never commit real secrets)
    if os.environ.get("ADMIN_TOKEN"):
        cfg.adminToken = os.environ["ADMIN_TOKEN"]
    if os.environ.get("DATABASE_URL"):
        cfg.databaseUrl = os.environ["DATABASE_URL"]
    if os.environ.get("ONEBOT_ACCESS_TOKEN"):
        cfg.onebot.accessToken = os.environ["ONEBOT_ACCESS_TOKEN"]
    if os.environ.get("ONEBOT_API_BASE"):
        cfg.onebot.apiBase = os.environ["ONEBOT_API_BASE"]
    if os.environ.get("WECOM_WEBHOOK_URL"):
        cfg.wecom.webhookUrl = os.environ["WECOM_WEBHOOK_URL"]
        if cfg.wecom.webhookUrl:
            cfg.wecom.enabled = True

    _CONFIG = cfg
    return cfg


def get_config() -> AppConfig:
    return load_config()
