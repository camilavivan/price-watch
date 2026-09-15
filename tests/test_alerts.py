"""Tests for alert evaluation (target / drop% / drop¥ / history_low / noise)."""

from __future__ import annotations

import unittest

from app import config as config_mod
from app.config import AlertsConfig, AppConfig, FetchConfig
from app.models import Product
from app.services import LocalHistoryStats, is_near_history_low, should_alert


def _cfg(**alert_kw) -> None:
    alerts = dict(
        onBelowTarget=True,
        dropPercent=5,
        dropYuan=0,
        onHistoryLow=True,
        historyLowDays=90,
        historyLowTolerancePercent=0.5,
    )
    alerts.update(alert_kw)
    config_mod._CONFIG = AppConfig(
        alerts=AlertsConfig(**alerts),
        fetch=FetchConfig(rateLimitSeconds=0, priceNoisePercent=0.5),
    )


class TestAlerts(unittest.TestCase):
    def tearDown(self):
        config_mod._CONFIG = None

    def _product(self, **kw) -> Product:
        base = dict(id=1, name="t", platform="jd", url="https://item.jd.com/1.html")
        base.update(kw)
        return Product(**base)

    def test_below_target(self):
        _cfg(dropPercent=0, onHistoryLow=False)
        p = self._product(target_price=100.0)
        alerted, reason = should_alert(p, 120.0, 99.0)
        self.assertTrue(alerted)
        self.assertIn("目标价", reason)

    def test_drop_percent(self):
        _cfg(onBelowTarget=False, dropPercent=5, onHistoryLow=False)
        p = self._product()
        alerted, reason = should_alert(p, 100.0, 90.0)
        self.assertTrue(alerted)
        self.assertIn("%", reason)

    def test_drop_yuan(self):
        _cfg(onBelowTarget=False, dropPercent=0, dropYuan=10, onHistoryLow=False)
        p = self._product()
        alerted, reason = should_alert(p, 100.0, 85.0)
        self.assertTrue(alerted)
        self.assertIn("¥", reason)

    def test_noise_suppresses_tiny_drop_alert(self):
        _cfg(onBelowTarget=False, dropPercent=0.1, dropYuan=0, onHistoryLow=False)
        p = self._product()
        # 0.3% drop within 0.5% noise → no drop alert
        alerted, _ = should_alert(p, 100.0, 99.7)
        self.assertFalse(alerted)

    def test_history_low_enter(self):
        _cfg(onBelowTarget=False, dropPercent=0, dropYuan=0, onHistoryLow=True)
        p = self._product()
        stats = LocalHistoryStats(
            days=90, count=5, lowest=100.0, highest=120.0, avg=110.0, prices=[120, 110, 100]
        )
        alerted, reason = should_alert(p, 110.0, 100.0, hist_stats=stats)
        self.assertTrue(alerted)
        self.assertIn("历史最低", reason)

    def test_history_low_already_at_low(self):
        _cfg(onBelowTarget=False, dropPercent=0, onHistoryLow=True)
        p = self._product()
        stats = LocalHistoryStats(
            days=90, count=5, lowest=100.0, highest=120.0, avg=110.0, prices=[100]
        )
        alerted, _ = should_alert(p, 100.0, 100.0, hist_stats=stats)
        self.assertFalse(alerted)

    def test_history_low_refresh(self):
        _cfg(onBelowTarget=False, dropPercent=0, onHistoryLow=True)
        p = self._product()
        stats = LocalHistoryStats(
            days=90, count=5, lowest=100.0, highest=120.0, avg=110.0, prices=[100]
        )
        alerted, reason = should_alert(p, 100.0, 98.0, hist_stats=stats)
        self.assertTrue(alerted)
        self.assertIn("刷新", reason)

    def test_near_history_low_helper(self):
        self.assertTrue(is_near_history_low(100.0, 100.0, 0.5))
        self.assertTrue(is_near_history_low(100.4, 100.0, 0.5))
        self.assertFalse(is_near_history_low(101.0, 100.0, 0.5))


if __name__ == "__main__":
    unittest.main()
