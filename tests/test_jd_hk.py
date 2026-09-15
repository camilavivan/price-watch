"""Offline tests: JD.hk canonical preserve + HK HTML/JSON price+tax extract."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.adapters.jd import (
    _candidate_page_urls,
    parse_jd_price_tax,
)
from app.url_normalize import (
    is_short_link,
    jd_product_canonical,
    normalize_url,
    extract_jd_sku,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestJdHkCanonical(unittest.TestCase):
    def test_mitem_preserve(self):
        u = "https://mitem.jd.hk/product/10066842682891.html?utm_source=share"
        info = normalize_url(u)
        self.assertEqual(info.platform, "jd")
        self.assertEqual(info.sku_id, "10066842682891")
        self.assertEqual(
            info.canonical_url,
            "https://mitem.jd.hk/product/10066842682891.html",
        )
        self.assertFalse(is_short_link(u))

    def test_npcitem_preserve(self):
        u = "https://npcitem.jd.hk/10066842682891.html"
        info = normalize_url(u)
        self.assertEqual(info.sku_id, "10066842682891")
        self.assertEqual(info.canonical_url, "https://npcitem.jd.hk/10066842682891.html")

    def test_item_jd_hk_preserve(self):
        u = "https://item.jd.hk/10066842682891.html"
        info = normalize_url(u)
        self.assertEqual(info.canonical_url, "https://item.jd.hk/10066842682891.html")

    def test_mainland_still_item_jd_com(self):
        u = "https://item.m.jd.com/product/100012043978.html"
        info = normalize_url(u)
        self.assertEqual(info.canonical_url, "https://item.jd.com/100012043978.html")

    def test_jd_product_canonical_helper(self):
        self.assertEqual(
            jd_product_canonical("10066842682891", "https://mitem.jd.hk/product/1.html"),
            "https://mitem.jd.hk/product/10066842682891.html",
        )
        self.assertEqual(
            jd_product_canonical("42", "https://item.jd.com/42.html"),
            "https://item.jd.com/42.html",
        )

    def test_extract_sku_mitem(self):
        self.assertEqual(
            extract_jd_sku("https://mitem.jd.hk/product/10066842682891.html"),
            "10066842682891",
        )


class TestJdHkCandidateOrder(unittest.TestCase):
    def test_prefers_mitem_first(self):
        pref = "https://mitem.jd.hk/product/10066842682891.html"
        urls = _candidate_page_urls("10066842682891", pref)
        self.assertTrue(urls[0].startswith("https://mitem.jd.hk/"))
        self.assertIn("https://npcitem.jd.hk/10066842682891.html", urls)
        self.assertIn("https://item.jd.com/10066842682891.html", urls)


class TestJdHkFixtures(unittest.TestCase):
    def test_mitem_fixture_price_tax(self):
        html = (FIXTURES / "mitem_jd_hk_10066842682891.html").read_text(encoding="utf-8")
        r = parse_jd_price_tax(html)
        self.assertEqual(r["list_price"], 318.0)
        self.assertEqual(r["tax_amount"], 41.34)
        self.assertIsNotNone(r["note"])

    def test_npcitem_json_fixture(self):
        raw = (FIXTURES / "npcitem_wareBusiness_10066842682891.json").read_text(
            encoding="utf-8"
        )
        r = parse_jd_price_tax(raw)
        self.assertEqual(r["list_price"], 318.0)
        self.assertEqual(r["tax_amount"], 41.34)

    def test_npcitem_html_snippet(self):
        html = (FIXTURES / "npcitem_jd_hk_snippet.html").read_text(encoding="utf-8")
        r = parse_jd_price_tax(html)
        # Prefer split 商品价+税费 when both present via JSON fields
        self.assertEqual(r["list_price"], 199.0)
        self.assertEqual(r["tax_amount"], 25.5)


if __name__ == "__main__":
    unittest.main()
