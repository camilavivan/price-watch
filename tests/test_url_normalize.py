"""Tests for URL normalize / platform detect / sku extract / share-paste extract."""

from __future__ import annotations

import unittest

from app.url_normalize import (
    UnknownPlatformError,
    detect_platform,
    extract_first_url,
    extract_jd_sku,
    extract_pdd_goods_id,
    extract_taobao_id,
    is_short_link,
    normalize_url,
    strip_tracking_params,
    strip_url_trailing_junk,
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

    def test_detect_short_hosts(self):
        self.assertEqual(detect_platform("https://m.tb.cn/h.xxx"), "taobao")
        self.assertEqual(detect_platform("https://tb.cn/h.abc"), "taobao")
        self.assertEqual(detect_platform("https://e.tb.cn/h.abc"), "taobao")
        self.assertEqual(detect_platform("https://s.tb.cn/h.abc"), "taobao")
        self.assertEqual(detect_platform("https://a.m.taobao.com/i123.htm"), "taobao")
        self.assertEqual(detect_platform("https://3.jd.hk/abc123"), "jd")
        self.assertEqual(detect_platform("https://u.jd.com/xxx"), "jd")
        self.assertEqual(detect_platform("https://3.cn/abc"), "jd")
        self.assertEqual(detect_platform("https://3.jd.com/x"), "jd")
        self.assertEqual(detect_platform("https://p.pinduoduo.com/xxx"), "pdd")
        self.assertTrue(is_short_link("https://m.tb.cn/h.xxx"))
        self.assertTrue(is_short_link("https://3.jd.hk/abc"))
        self.assertFalse(is_short_link("https://item.jd.com/100.html"))

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

    def test_strip_trailing_paren(self):
        self.assertEqual(
            strip_url_trailing_junk("https://item.jd.com/100.html)"),
            "https://item.jd.com/100.html",
        )
        self.assertEqual(
            strip_url_trailing_junk("https://m.tb.cn/h.xxx）。"),
            "https://m.tb.cn/h.xxx",
        )
        info = normalize_url("https://item.jd.com/100012043978.html）")
        self.assertEqual(info.sku_id, "100012043978")
        self.assertEqual(info.platform, "jd")

    def test_extract_url_from_jd_share_paste(self):
        paste = (
            "【京东】https://3.jd.hk/2S8abc 点击链接直接打开\n"
            "或者复制这条信息￥ABC￥打开京东\n"
            "粉丝福利购🎉 监控"
        )
        url = extract_first_url(paste)
        self.assertEqual(url, "https://3.jd.hk/2S8abc")
        self.assertEqual(detect_platform(url), "jd")

    def test_extract_url_from_taobao_share_paste(self):
        paste = (
            "【淘宝】这个好用啊 https://m.tb.cn/h.5KxYzW  CZ0001 "
            "点击链接，再选择浏览器咑幵；或復制这段描述$/xx$后到淘宝\n"
            "淘口令 粉丝福利购"
        )
        url = extract_first_url(paste)
        self.assertEqual(url, "https://m.tb.cn/h.5KxYzW")
        self.assertEqual(detect_platform(url), "taobao")

    def test_extract_url_strips_trailing_paren_in_text(self):
        paste = "请看 (https://item.jd.com/100012043978.html) 谢谢"
        url = extract_first_url(paste)
        self.assertEqual(url, "https://item.jd.com/100012043978.html")

    def test_normalize_accepts_already_resolved_jd(self):
        # After resolve_url, short link becomes item.jd.com — normalize must work
        resolved = "https://item.jd.com/100012043978.html?utm_source=share"
        info = normalize_url(resolved, require_known_platform=True)
        self.assertEqual(info.platform, "jd")
        self.assertEqual(info.sku_id, "100012043978")
        self.assertEqual(info.canonical_url, "https://item.jd.com/100012043978.html")


if __name__ == "__main__":
    unittest.main()
