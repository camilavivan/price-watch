"""Unit tests: Playwright price extract from fixtures (no live browser)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.browser.extract import (
    extract_price_from_dom_text,
    extract_price_from_ware_json,
    merge_playwright_hits,
)
from app.adapters.jd import parse_jd_price_tax

FIXTURES = Path(__file__).parent / "fixtures"


class TestWareJsonExtract(unittest.TestCase):
    def test_a2_fixture(self):
        raw = (FIXTURES / "npcitem_wareBusiness_10066842682891.json").read_text(
            encoding="utf-8"
        )
        r = extract_price_from_ware_json(raw)
        self.assertEqual(r["list_price"], 318.0)
        self.assertEqual(r["tax_amount"], 41.34)
        self.assertIn("a2", str(r.get("title") or ""))

    def test_dict_direct(self):
        r = extract_price_from_ware_json(
            {"priceInfo": {"pPrice": "100", "taxFee": "13"}}
        )
        self.assertEqual(r["list_price"], 100.0)
        self.assertEqual(r["tax_amount"], 13.0)

    def test_allin_only(self):
        r = extract_price_from_ware_json(
            {"priceInfo": {"plusTaxPrice": "359.34"}}
        )
        self.assertEqual(r["list_price"], 359.34)
        self.assertEqual(r["tax_amount"], 0.0)


class TestDomAndMerge(unittest.TestCase):
    def test_dom_text(self):
        r = extract_price_from_dom_text("商品价：￥318.00\n预估税费：￥41.34")
        self.assertEqual(r["list_price"], 318.0)
        self.assertEqual(r["tax_amount"], 41.34)

    def test_merge_prefers_first_price(self):
        m = merge_playwright_hits(
            {"list_price": 318.0, "tax_amount": 41.34},
            {"list_price": 999.0, "title": "x"},
        )
        self.assertEqual(m["list_price"], 318.0)
        self.assertEqual(m["title"], "x")


class TestHtmlFixtureReuse(unittest.TestCase):
    def test_mitem_html(self):
        html = (FIXTURES / "mitem_jd_hk_10066842682891.html").read_text(encoding="utf-8")
        r = parse_jd_price_tax(html)
        self.assertEqual(r.get("list_price"), 318.0)
        self.assertAlmostEqual(float(r.get("tax_amount") or 0), 41.34, places=2)


class TestSessionStatus(unittest.TestCase):
    def test_status_dict_safe(self):
        from app.browser.jd_session import status_dict

        st = status_dict()
        self.assertIn("playwright_enabled", st)
        self.assertIn("has_storage_state", st)


if __name__ == "__main__":
    unittest.main()
