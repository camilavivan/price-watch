"""Unit tests: price plausibility / login wall / history bogus checks."""

from __future__ import annotations

import unittest

from app.browser.extract import extract_price_from_dom_text, merge_playwright_hits
from app.price_sanity import (
    is_bogus_vs_history,
    is_login_wall_text,
    is_plausible_retail,
    looks_like_unit_spec,
    reject_manual_price_reason,
)


class TestPlausibleRetail(unittest.TestCase):
    def test_reject_two_yuan_unlabeled(self):
        self.assertFalse(is_plausible_retail(2.0, min_plausible=10))

    def test_accept_decimal_below_min(self):
        self.assertTrue(is_plausible_retail(9.9, min_plausible=10, has_decimal=True))

    def test_accept_labeled_small(self):
        self.assertTrue(is_plausible_retail(2.0, min_plausible=10, labeled=True))

    def test_force(self):
        self.assertTrue(is_plausible_retail(1.0, min_plausible=10, force=True))

    def test_normal(self):
        self.assertTrue(is_plausible_retail(183.0, min_plausible=10))


class TestHistoryBogus(unittest.TestCase):
    def test_two_vs_formula_history(self):
        self.assertTrue(is_bogus_vs_history(2.0, 183.0, fraction=0.2))
        self.assertTrue(is_bogus_vs_history(2.0, 183.0, fraction=0.3))

    def test_sale_ok(self):
        self.assertFalse(is_bogus_vs_history(150.0, 183.0, fraction=0.2))

    def test_no_history(self):
        self.assertFalse(is_bogus_vs_history(2.0, None))


class TestRejectReason(unittest.TestCase):
    def test_spec_message(self):
        r = reject_manual_price_reason(2.0, min_plausible=10)
        self.assertIsNotNone(r)
        self.assertIn("2", r)
        self.assertIn("规格", r)

    def test_history_message(self):
        r = reject_manual_price_reason(
            5.0, min_plausible=1, history_lowest=200.0, history_fraction=0.2
        )
        self.assertIsNotNone(r)
        self.assertIn("历史", r)

    def test_ok(self):
        self.assertIsNone(
            reject_manual_price_reason(200.0, min_plausible=10, history_lowest=183.0)
        )

    def test_unit_spec_in_text(self):
        self.assertTrue(looks_like_unit_spec("a2紫白金奶粉2段", 2.0))
        self.assertFalse(looks_like_unit_spec("到手价 200", 200.0))


class TestLoginWall(unittest.TestCase):
    def test_markers(self):
        self.assertTrue(is_login_wall_text("商品价 ¥??? 登录查看价格"))
        self.assertTrue(is_login_wall_text("请登录后可查看完整价格"))
        self.assertFalse(is_login_wall_text("商品价：￥318.00"))

    def test_dom_extract_skips_wall(self):
        r = extract_price_from_dom_text("登录查看价格 ¥??? 还有杂讯 2")
        self.assertNotIn("list_price", r)

    def test_dom_extract_ok(self):
        r = extract_price_from_dom_text("商品价：￥318.00\n预估税费：￥41.34")
        self.assertEqual(r["list_price"], 318.0)

    def test_merge_rejects_tiny(self):
        m = merge_playwright_hits({"list_price": 2.0, "tax_amount": 0})
        self.assertIsNone(m.get("list_price"))


class TestJdParseLoginWall(unittest.TestCase):
    def test_parse_login_wall(self):
        from app.adapters.jd import parse_jd_price_tax

        r = parse_jd_price_tax("<html>登录查看价格 ¥???</html>")
        self.assertIsNone(r.get("list_price"))
        self.assertIn("登录", str(r.get("note") or ""))


if __name__ == "__main__":
    unittest.main()
