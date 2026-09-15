"""Unit tests for local history helpers (no DB)."""

from __future__ import annotations

import unittest

from app.services import is_near_history_low, sparkline, should_alert
from app.models import Product
from app.config import AlertsConfig, AppConfig, load_config
from app import config as config_mod
from app.services import LocalHistoryStats


class TestLocalHistory(unittest.TestCase):
    def test_sparkline_flat(self):
        s = sparkline([10.0, 10.0, 10.0])
        self.assertEqual(len(s), 3)
        self.assertTrue(all(c == s[0] for c in s))

    def test_sparkline_rising(self):
        s = sparkline([1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(len(s), 8)
        self.assertLessEqual(s[0], s[-1])  # unicode order roughly increases

    def test_near_history_low(self):
        self.assertTrue(is_near_history_low(100.0, 100.0, 0.5))
        self.assertTrue(is_near_history_low(100.4, 100.0, 0.5))  # within 0.5%
        self.assertFalse(is_near_history_low(101.0, 100.0, 0.5))
        self.assertFalse(is_near_history_low(99.0, None, 0.5))

    def test_should_alert_history_low(self):
        # Isolate config
        config_mod._CONFIG = AppConfig(
            alerts=AlertsConfig(
                onBelowTarget=False,
                dropPercent=0,
                dropYuan=0,
                onHistoryLow=True,
                historyLowDays=90,
                historyLowTolerancePercent=0.5,
            )
        )
        p = Product(
            id=1,
            name="t",
            platform="jd",
            url="https://item.jd.com/1.html",
        )
        stats = LocalHistoryStats(
            days=90, count=5, lowest=100.0, highest=120.0, avg=110.0, prices=[120, 110, 100]
        )
        # Drop into low zone from above
        alerted, reason = should_alert(p, 110.0, 100.0, hist_stats=stats)
        self.assertTrue(alerted)
        self.assertIn("历史最低", reason)

        # Already at low, same price → no new history_low reason
        alerted2, reason2 = should_alert(p, 100.0, 100.0, hist_stats=stats)
        self.assertFalse(alerted2)

        config_mod._CONFIG = None  # reset


if __name__ == "__main__":
    unittest.main()
