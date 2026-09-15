"""ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("owner_openid", "url", name="uq_owner_url"),
        Index("ix_products_owner_openid", "owner_openid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)  # jd / taobao / pdd
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sku_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # QQ 官方开放平台用户 openid；Web 管理端添加可为空
    owner_openid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Price components (yuan)
    list_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    coupon_amount: Mapped[float] = mapped_column(Float, default=0.0)
    full_reduction: Mapped[float] = mapped_column(Float, default=0.0)
    rebate_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    landing_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    target_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Optional product image URL for QQ alerts
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Adapter status
    last_check_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    needs_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Per-product alert overrides (NULL = use global)
    alert_drop_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    alert_drop_yuan: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def compute_landing(self) -> Optional[float]:
        """到手价 = 标价 - 券 - 满减（不含返利）."""
        if self.list_price is None:
            return self.landing_price
        v = self.list_price - (self.coupon_amount or 0) - (self.full_reduction or 0)
        return round(max(v, 0), 2)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    list_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    coupon_amount: Mapped[float] = mapped_column(Float, default=0.0)
    full_reduction: Mapped[float] = mapped_column(Float, default=0.0)
    rebate_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    landing_price: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="check")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
