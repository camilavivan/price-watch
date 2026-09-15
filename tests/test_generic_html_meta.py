"""Unit tests for HTML meta (title / image) extraction — no network."""

from __future__ import annotations

import unittest

from app.adapters.generic_html import (
    extract_from_html,
    extract_meta_only,
    normalize_image_url,
)


SAMPLE = """
<!DOCTYPE html>
<html>
<head>
  <meta property="og:title" content="测试商品标题 - 京东" />
  <meta property="og:image" content="//img14.360buyimg.com/n1/jfs/t1/abc/123.jpg" />
  <meta name="twitter:image" content="https://img.alicdn.com/imgextra/i1/foo.jpg" />
  <meta itemprop="name" content="itemprop备用标题" />
  <meta itemprop="image" content="https://example.com/itemprop.jpg" />
  <title>页面标题_淘宝</title>
</head>
<body>
  <script>
    var sku = {"imagePath":"jfs/t1/path/to/main.jpg","price":"99.00"};
  </script>
  <div>￥99.00</div>
</body>
</html>
"""


class TestGenericHtmlMeta(unittest.TestCase):
    def test_normalize_protocol_relative(self):
        self.assertEqual(
            normalize_image_url("//img14.360buyimg.com/n1/jfs/t1/x.jpg"),
            "https://img14.360buyimg.com/n1/jfs/t1/x.jpg",
        )

    def test_normalize_absolute(self):
        self.assertEqual(
            normalize_image_url("https://img.alicdn.com/a.jpg"),
            "https://img.alicdn.com/a.jpg",
        )

    def test_normalize_relative_without_base_skipped(self):
        self.assertIsNone(normalize_image_url("/static/a.jpg"))

    def test_normalize_jd_image_path(self):
        self.assertEqual(
            normalize_image_url("jfs/t1/path/to/main.jpg"),
            "https://img14.360buyimg.com/n1/jfs/t1/path/to/main.jpg",
        )

    def test_extract_meta_og_title_image(self):
        title, image = extract_meta_only(SAMPLE)
        self.assertEqual(title, "测试商品标题")
        self.assertEqual(image, "https://img14.360buyimg.com/n1/jfs/t1/abc/123.jpg")

    def test_extract_from_html_keeps_meta_when_price_ok(self):
        r = extract_from_html(SAMPLE)
        self.assertTrue(r.ok)
        self.assertEqual(r.price, 99.0)
        self.assertEqual(r.title, "测试商品标题")
        self.assertTrue(r.image_url and r.image_url.startswith("https://"))

    def test_extract_from_html_keeps_meta_when_price_fails(self):
        html = """
        <html><head>
          <meta property="og:title" content="无价格商品" />
          <meta property="og:image" content="https://img14.360buyimg.com/n1/x.jpg" />
        </head><body>hello</body></html>
        """
        r = extract_from_html(html)
        self.assertFalse(r.ok)
        self.assertEqual(r.title, "无价格商品")
        self.assertEqual(r.image_url, "https://img14.360buyimg.com/n1/x.jpg")

    def test_cdn_patterns_pdd(self):
        html = '<html><body><img src="//t00img.yangkeduo.com/goods/images/2020-01-01/abc.jpg" /></body></html>'
        title, image = extract_meta_only(html)
        self.assertIsNone(title)
        self.assertEqual(
            image,
            "https://t00img.yangkeduo.com/goods/images/2020-01-01/abc.jpg",
        )

    def test_json_image_url_field(self):
        html = '{"mainImage":"https://img.alicdn.com/imgextra/main.png","name":"x"}'
        # pad to pass length check
        html = html + (" " * 40)
        _, image = extract_meta_only(html)
        self.assertEqual(image, "https://img.alicdn.com/imgextra/main.png")


if __name__ == "__main__":
    unittest.main()
