"""Tests for URL normalize / platform detect / sku extract."""

from __future__ import annotations

import unittest

from app.url_normalize import (
    UnknownPlatformError,
    detect_platform,
    extract_jd_sku,
    extract_pdd_goods_id,
    extract_taobao_id,
    normalize_url,
    strip_tracking_params,
)


class TestUrlNormalize(unittest.TestCase):
    def test_detect_platforms(self):
        self.assertEqual(detect_platform("https://item.jd.com/100012043978.html"), "jd")
        self.assertEqual(detect_platform("https://item.m.jd.com/product/100012043978.html"), "jd")
        self.assertEqual(detect_platform("https://item.taobao.com/item.htm?id=12345678901"), "taobao")
        self.assertEqual(detect_platform("https://detail.tmall.com/item.htm?id=12345678901"), "taobao")
        self.assertEqual(
            detect_platform("https://mobile.yangkeduo.com/goods.html?goods_id=123456789"),
            "pdd",
        )
        self.assertIsNone(detect_platform("https://www.example.com/x"))

    def test_jd_sku_and_canonical(self):
        dirty = (
            "https://item.jd.com/100012043978.html?utm_source=share&jd_pop=1"
            "&extension_id=abc&from=wx"
        )
        info = normalize_url(dirty)
        self.assertEqual(info.platform, "jd")
        self.assertEqual(info.sku_id, "100012043978")
        self.assertEqual(info.canonical_url, "https://item.jd.com/100012043978.html")
        self.assertEqual(extract_jd_sku(dirty), "100012043978")

    def test_jd_mobile(self):
        u = "https://item.m.jd.com/product/12345678901.html?utm_campaign=x"
        info = normalize_url(u)
        self.assertEqual(info.platform, "jd")
        self.assertEqual(info.sku_id, "12345678901")
        self.assertEqual(info.canonical_url, "https://item.jd.com/12345678901.html")

    def test_taobao_id(self):
        dirty = "https://item.taobao.com/item.htm?id=65432109876&spm=a1.2.3&utm_medium=cpc"
        info = normalize_url(dirty)
        self.assertEqual(info.platform, "taobao")
        self.assertEqual(info.sku_id, "65432109876")
        self.assertEqual(info.canonical_url, "https://item.taobao.com/item.htm?id=65432109876")
        self.assertEqual(extract_taobao_id(dirty), "65432109876")

    def test_tmall_id(self):
        u = "https://detail.tmall.com/item.htm?id=99887766554&spm=xx"
        info = normalize_url(u)
        self.assertEqual(info.platform, "taobao")
        self.assertEqual(info.sku_id, "99887766554")
        self.assertIn("tmall.com", info.canonical_url)
        self.assertIn("id=99887766554", info.canonical_url)

    def test_pdd_goods_id(self):
        dirty = (
            "https://mobile.yangkeduo.com/goods.html?goods_id=11223344556"
            "&share_id=zzz&utm_source=copy"
        )
        info = normalize_url(dirty)
        self.assertEqual(info.platform, "pdd")
        self.assertEqual(info.sku_id, "11223344556")
        self.assertEqual(
            info.canonical_url,
            "https://mobile.yangkeduo.com/goods.html?goods_id=11223344556",
        )
        self.assertEqual(extract_pdd_goods_id(dirty), "11223344556")

    def test_strip_tracking(self):
        u = strip_tracking_params(
            "https://item.jd.com/1.html?utm_source=a&utm_campaign=b&id=keepme"
        )
        self.assertNotIn("utm_", u)
        # id kept when present
        self.assertIn("id=keepme", u)

    def test_unknown_platform_rejected(self):
        with self.assertRaises(UnknownPlatformError):
            normalize_url("https://www.amazon.com/dp/B00", require_known_platform=True)

    def test_scheme_optional(self):
        info = normalize_url("item.jd.com/42.html")
        self.assertEqual(info.platform, "jd")
        self.assertEqual(info.sku_id, "42")


if __name__ == "__main__":
    unittest.main()
