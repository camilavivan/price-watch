"""Unit tests: cookie parse → storage_state; login markers; login-wall reject."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.browser.cookies_common import (
    cookie_dict_to_storage_state,
    detect_login_markers,
    looks_logged_in,
    parse_cookie_header,
    save_cookie_bundle,
)
from app.browser.jd_cookies import (
    cookie_dict_to_storage_state as jd_state,
    detect_login_markers as jd_markers,
    looks_logged_in as jd_logged,
    parse_cookie_header as jd_parse,
)
from app.adapters.jd import parse_jd_price_tax
from app.price_sanity import is_login_wall_text


class TestParseCookieHeader(unittest.TestCase):
    def test_basic(self):
        d = parse_cookie_header("thor=abc; pin=user1; pinId=123")
        self.assertEqual(d["thor"], "abc")
        self.assertEqual(d["pin"], "user1")
        self.assertEqual(d["pinId"], "123")

    def test_cookie_prefix(self):
        d = parse_cookie_header("Cookie: unb=1; tracknick=nick")
        self.assertEqual(d["unb"], "1")
        self.assertIn("tracknick", d)

    def test_multiline(self):
        d = parse_cookie_header("thor=x\npin=y")
        self.assertEqual(d["thor"], "x")
        self.assertEqual(d["pin"], "y")

    def test_skips_set_cookie_attrs(self):
        d = parse_cookie_header("thor=a; Path=/; Domain=.jd.com; pin=b")
        self.assertEqual(d["thor"], "a")
        self.assertEqual(d["pin"], "b")
        self.assertNotIn("Path", d)
        self.assertNotIn("Domain", d)


class TestJdMarkers(unittest.TestCase):
    def test_product_crawling_style(self):
        cookies = {"thor": "t", "pin": "p", "pinId": "1", "other": "x"}
        markers = detect_login_markers(cookies, "jd")
        self.assertIn("thor", markers)
        self.assertIn("pin", markers)
        self.assertIn("pinId", markers)
        self.assertTrue(looks_logged_in(cookies, "jd"))
        self.assertTrue(jd_logged(cookies))
        self.assertEqual(set(jd_markers(cookies)), set(markers))

    def test_empty(self):
        self.assertFalse(looks_logged_in({}, "jd"))
        self.assertFalse(looks_logged_in({"__jda": "1"}, "jd"))


class TestTaobaoPddMarkers(unittest.TestCase):
    def test_taobao(self):
        cookies = {"unb": "1", "tracknick": "n", "_nk_": "n", "cookie2": "c"}
        self.assertTrue(looks_logged_in(cookies, "taobao"))
        m = detect_login_markers(cookies, "taobao")
        self.assertTrue(set(m) >= {"unb", "tracknick"})

    def test_pdd(self):
        cookies = {"PDDAccessToken": "tok", "pdd_user_id": "9"}
        self.assertTrue(looks_logged_in(cookies, "pdd"))


class TestStorageState(unittest.TestCase):
    def test_jd_storage_has_domains(self):
        state = cookie_dict_to_storage_state({"thor": "abc", "pin": "u"}, "jd")
        self.assertIn("cookies", state)
        domains = {c["domain"] for c in state["cookies"]}
        self.assertIn(".jd.com", domains)
        self.assertIn(".jd.hk", domains)
        names = {c["name"] for c in state["cookies"]}
        self.assertIn("thor", names)
        # wrapper parity
        state2 = jd_state({"thor": "abc"})
        self.assertTrue(any(c["name"] == "thor" for c in state2["cookies"]))

    def test_save_bundle_writes_files(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch(
                "app.browser.cookies_common.browser_data_dir", return_value=td_path
            ), mock.patch(
                "app.browser.cookies_common.storage_path_for",
                side_effect=lambda p: td_path / f"{p}_storage.json",
            ), mock.patch(
                "app.browser.cookies_common.cookie_path_for",
                side_effect=lambda p: td_path / f"{p}_cookie.txt",
            ):
                r = save_cookie_bundle("jd", "thor=SECRET; pin=me; pinId=9")
                self.assertTrue(r["ok"])
                self.assertTrue(r["logged_in"])
                self.assertIn("thor", r["markers"])
                sp = td_path / "jd_storage.json"
                cp = td_path / "jd_cookie.txt"
                self.assertTrue(sp.is_file())
                self.assertTrue(cp.is_file())
                data = json.loads(sp.read_text(encoding="utf-8"))
                self.assertTrue(any(c["name"] == "thor" for c in data["cookies"]))
                self.assertIn("thor=SECRET", cp.read_text(encoding="utf-8"))


class TestLoginWallReject(unittest.TestCase):
    def test_markers(self):
        self.assertTrue(is_login_wall_text("登录查看价格 ¥???"))
        self.assertFalse(is_login_wall_text("现价￥318.00"))

    def test_parse_jd_rejects_wall(self):
        r = parse_jd_price_tax("<html>登录查看价格 ￥???</html>")
        self.assertIsNone(r.get("list_price"))
        self.assertIn("登录", str(r.get("note") or ""))

    def test_parse_rejects_question_marks_only_page(self):
        # Wall markers in price_sanity
        html = '<div class="price">¥???</div><p>登录后可查看</p>'
        self.assertTrue(is_login_wall_text(html))
        r = parse_jd_price_tax(html)
        self.assertIsNone(r.get("list_price"))


if __name__ == "__main__":
    unittest.main()
