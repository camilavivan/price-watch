"""Tests for URL normalize / platform detect / sku extract / share-paste extract."""

from __future__ import annotations

import unittest

from app.url_normalize import (
    UnknownPlatformError,
    detect_platform,
    extract_best_url,
    extract_first_url,
    extract_jd_sku,
    extract_jd_sku_from_html,
    extract_pdd_goods_id,
    extract_taobao_id,
    extract_title_hint,
    extract_url_from_html,
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
        self.assertTrue(is_short_link("https://s.click.taobao.com/t?e=xxx"))
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

    def test_extract_url_with_taokouling_junk_emoji(self):
        """淘口令 pastes often wrap URL with 💲🔐 and Chinese — still extract m.tb.cn."""
        paste = "监控 59💲4luGT8mrU6g🔐 https://m.tb.cn/h.8rOD8bO  CZ028 粉丝福利购"
        url = extract_first_url(paste)
        self.assertEqual(url, "https://m.tb.cn/h.8rOD8bO")
        self.assertEqual(detect_platform(url), "taobao")
        self.assertTrue(is_short_link(url))

    def test_extract_url_jd_example_paste(self):
        paste = (
            "监控 【京东】https://3.jd.hk/1034a-WN "
            "「【询客服领券】a2紫白金奶粉2段」"
        )
        url = extract_first_url(paste)
        self.assertEqual(url, "https://3.jd.hk/1034a-WN")
        self.assertEqual(detect_platform(url), "jd")

    def test_prefer_commerce_short_host(self):
        paste = (
            "see https://www.example.com/promo and then "
            "https://m.tb.cn/h.abc123 for the deal"
        )
        self.assertEqual(extract_best_url(paste), "https://m.tb.cn/h.abc123")

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

    def test_extract_url_from_html_var_url(self):
        html = """
        <html><script>
        var url = 'https://item.taobao.com/item.htm?id=12345678901';
        location.href = url;
        </script></html>
        """
        self.assertEqual(
            extract_url_from_html(html),
            "https://item.taobao.com/item.htm?id=12345678901",
        )

    def test_extract_url_from_html_var_url_double_quotes(self):
        html = 'var url = "https://s.click.taobao.com/t?e=abc";'
        self.assertEqual(
            extract_url_from_html(html),
            "https://s.click.taobao.com/t?e=abc",
        )

    def test_extract_url_from_html_meta_refresh(self):
        html = (
            '<html><head><meta http-equiv="refresh" '
            'content="0;url=https://item.jd.com/100012043978.html"></head></html>'
        )
        self.assertEqual(
            extract_url_from_html(html),
            "https://item.jd.com/100012043978.html",
        )

    def test_extract_url_from_html_location_href(self):
        html = "window.location.href = 'https://detail.tmall.com/item.htm?id=99887766554';"
        self.assertEqual(
            extract_url_from_html(html),
            "https://detail.tmall.com/item.htm?id=99887766554",
        )

    def test_extract_url_from_html_og_url(self):
        html = (
            '<meta property="og:url" content="https://item.taobao.com/item.htm?id=11122233344">'
        )
        self.assertEqual(
            extract_url_from_html(html),
            "https://item.taobao.com/item.htm?id=11122233344",
        )

    def test_extract_url_from_html_item_link(self):
        html = (
            '<a href="https://item.jd.com/55566677788.html?utm_source=x">buy</a>'
        )
        self.assertEqual(
            extract_url_from_html(html),
            "https://item.jd.com/55566677788.html?utm_source=x",
        )

    def test_extract_jd_sku_from_html(self):
        html = 'var pageConfig = { skuId: 100012043978, name: "x" };'
        self.assertEqual(extract_jd_sku_from_html(html), "100012043978")

    def test_extract_title_hint_corner_brackets(self):
        paste = (
            "监控 【京东】https://3.jd.hk/1034a-WN "
            "「【询客服领券】a2紫白金奶粉2段」"
        )
        self.assertEqual(extract_title_hint(paste), "a2紫白金奶粉2段")

    def test_extract_title_hint_skips_platform_tag(self):
        paste = "【淘宝】https://m.tb.cn/h.xxx 粉丝福利购"
        # bare 【淘宝】 skipped; no other title → None
        self.assertIsNone(extract_title_hint(paste))

    def test_extract_title_hint_simple(self):
        paste = "监控 「有机纯牛奶」 https://item.jd.com/1.html"
        self.assertEqual(extract_title_hint(paste), "有机纯牛奶")


    def test_shoutao_h5_detail(self):
        """手淘 h5.m.taobao.com/awp/core/detail.htm?id= → canonical with id."""
        u = "https://h5.m.taobao.com/awp/core/detail.htm?id=654321098765&spm=a1.2"
        info = normalize_url(u)
        self.assertEqual(info.platform, "taobao")
        self.assertEqual(info.sku_id, "654321098765")
        self.assertEqual(info.canonical_url, "https://item.taobao.com/item.htm?id=654321098765")
        self.assertFalse(is_short_link(u))

    def test_shoutao_a_m_ihtm(self):
        """手淘 a.m.taobao.com/i{id}.htm → extract id, not treated as bare short link."""
        u = "https://a.m.taobao.com/i654321098765.htm"
        self.assertEqual(detect_platform(u), "taobao")
        self.assertEqual(extract_taobao_id(u), "654321098765")
        self.assertFalse(is_short_link(u))
        info = normalize_url(u)
        self.assertEqual(info.sku_id, "654321098765")
        self.assertEqual(info.canonical_url, "https://item.taobao.com/item.htm?id=654321098765")

    def test_shoutao_a_m_without_id_still_short(self):
        u = "https://a.m.taobao.com/share.htm"
        self.assertEqual(detect_platform(u), "taobao")
        self.assertTrue(is_short_link(u))

    def test_shoutao_market_m(self):
        u = "https://market.m.taobao.com/app/tb-source-app/shopdetail/pages/index?id=112233445566"
        self.assertEqual(detect_platform(u), "taobao")
        self.assertEqual(extract_taobao_id(u), "112233445566")
        info = normalize_url(u)
        self.assertEqual(info.canonical_url, "https://item.taobao.com/item.htm?id=112233445566")

    def test_e_tb_cn_short(self):
        u = "https://e.tb.cn/h.abcXYZ"
        self.assertEqual(detect_platform(u), "taobao")
        self.assertTrue(is_short_link(u))

    def test_extract_best_url_prefers_a_m_taobao(self):
        paste = (
            "see https://www.example.com/x and "
            "https://a.m.taobao.com/i654321098765.htm deal"
        )
        self.assertEqual(extract_best_url(paste), "https://a.m.taobao.com/i654321098765.htm")

    def test_html_embed_a_m_taobao(self):
        html = '<a href="https://a.m.taobao.com/i998877665544.htm">go</a>'
        self.assertEqual(
            extract_url_from_html(html),
            "https://a.m.taobao.com/i998877665544.htm",
        )


if __name__ == "__main__":
    unittest.main()
