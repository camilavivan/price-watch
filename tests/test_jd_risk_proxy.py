"""Unit tests: JD risk/SPA detection, unusable API body, outbound proxy helper."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app.adapters.http_util import async_client_kwargs, outbound_proxy
from app.adapters.jd import (
    JD_BLOCKED_ERROR,
    _is_unusable_api_body,
    _looks_like_risk_html,
    _looks_like_spa_shell_no_price,
    parse_jd_price_tax,
)
from app.services import compute_landing


class TestJdRiskHtml(unittest.TestCase):
    def test_risk_handler_markers(self):
        html = (
            "<html><head><title>京东验证</title></head>"
            "<body>cfe.m.jd.com/privatedomain/risk_handler?bp_bizid=1</body></html>"
        )
        self.assertTrue(_looks_like_risk_html(html))

    def test_risk_handler_path_only(self):
        html = "<html><body>redirect to risk_handler please wait</body></html>"
        self.assertTrue(_looks_like_risk_html(html))

    def test_normal_product_not_risk(self):
        html = (
            "<html><head><title>进口奶粉</title></head>"
            '<script>var pageConfig={"product":{"pPrice":"199.00","taxFee":"25.50"}};</script>'
            "</html>"
        )
        self.assertFalse(_looks_like_risk_html(html))


class TestJdSpaShell(unittest.TestCase):
    def test_spa_shell_pageconfig_no_price(self):
        html = (
            "<!doctype html><html><head><title>京东</title></head>"
            '<body><div id="app"></div>'
            "<script>window.pageConfig={skuId:'10066842682891',product:{name:'x'}};</script>"
            + ("x" * 2000)
            + "</body></html>"
        )
        self.assertTrue(_looks_like_spa_shell_no_price(html))
        self.assertFalse(_looks_like_risk_html(html))
        r = parse_jd_price_tax(html)
        self.assertIsNone(r["list_price"])

    def test_spa_with_price_not_flagged(self):
        html = (
            '<div id="app"></div>'
            '<script>window.pageConfig={"product":{"pPrice":"318.00","taxFee":"41.34"}};</script>'
        )
        self.assertFalse(_looks_like_spa_shell_no_price(html))
        r = parse_jd_price_tax(html)
        self.assertEqual(r["list_price"], 318.0)
        self.assertEqual(r["tax_amount"], 41.34)


class TestUnusableApiBody(unittest.TestCase):
    def test_echo_no_access(self):
        self.assertTrue(
            _is_unusable_api_body('{"echo":"no access","code":"error2"}')
        )

    def test_api_does_not_exist(self):
        self.assertTrue(
            _is_unusable_api_body('{"echo":"API does not exist"}')
        )

    def test_real_json_ok(self):
        self.assertFalse(
            _is_unusable_api_body('{"price":{"pPrice":"10.00"},"stock":1}')
        )


class TestBlockedErrorConstant(unittest.TestCase):
    def test_message_mentions_fill_and_proxy(self):
        self.assertIn("填价", JD_BLOCKED_ERROR)
        self.assertIn("代理", JD_BLOCKED_ERROR)
        self.assertIn("风控", JD_BLOCKED_ERROR)


class TestOutboundProxy(unittest.TestCase):
    def test_jd_http_proxy_override(self):
        env = {
            "HTTP_PROXY": "http://a:1",
            "HTTPS_PROXY": "http://b:2",
            "JD_HTTP_PROXY": "http://jd-only:7890",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            for k in ("ALL_PROXY", "all_proxy", "http_proxy", "https_proxy"):
                os.environ.pop(k, None)
            self.assertEqual(outbound_proxy(), "http://jd-only:7890")
            kw = async_client_kwargs(timeout=5.0)
            self.assertEqual(kw["proxy"], "http://jd-only:7890")
            self.assertEqual(kw["timeout"], 5.0)

    def test_https_proxy_when_no_jd(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ["HTTPS_PROXY"] = "http://clash:7890"
            self.assertEqual(outbound_proxy(), "http://clash:7890")


class TestComputeLandingStillOk(unittest.TestCase):
    def test_list_plus_tax(self):
        self.assertEqual(
            compute_landing(318.0, coupon=0, full_reduction=0, tax_amount=41.34),
            359.34,
        )


if __name__ == "__main__":
    unittest.main()
