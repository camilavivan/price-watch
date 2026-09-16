"""Unit tests for manmanbuy history helpers (mocked HTML / datePrice)."""

from __future__ import annotations

import unittest

from app.history_external import (
    build_history_token,
    extract_ticket_from_html,
    parse_date_price,
    series_from_points,
    ticket_to_basic_auth,
)


class TestManmanbuyAuth(unittest.TestCase):
    def test_extract_ticket_simple(self):
        html = '<input type="hidden" id="ticket" value="ABCDEFGH1234" />'
        self.assertEqual(extract_ticket_from_html(html), "ABCDEFGH1234")

    def test_extract_ticket_attr_order(self):
        html = '<input value="ZZYYXXWW9988" id="ticket" type="hidden">'
        self.assertEqual(extract_ticket_from_html(html), "ZZYYXXWW9988")

    def test_extract_ticket_missing(self):
        self.assertIsNone(extract_ticket_from_html("<html></html>"))
        self.assertIsNone(extract_ticket_from_html(""))

    def test_ticket_to_basic_auth_rotate(self):
        # last 4 → front: ABCDEFGH → EFGHABCD
        self.assertEqual(ticket_to_basic_auth("ABCDEFGH"), "BasicAuth EFGHABCD")
        self.assertEqual(ticket_to_basic_auth("abcd"), "BasicAuth abcd")  # len==4 no rotate
        self.assertEqual(ticket_to_basic_auth("abc"), "BasicAuth abc")

    def test_build_history_token_deterministic(self):
        tok = build_history_token(
            method="getHistoryTrend",
            key="https://item.jd.com/6290488.html",
            t="1610601399358",
        )
        self.assertEqual(len(tok), 32)
        self.assertEqual(tok, tok.upper())
        # Same inputs → same token
        tok2 = build_history_token(
            method="getHistoryTrend",
            key="https://item.jd.com/6290488.html",
            t="1610601399358",
        )
        self.assertEqual(tok, tok2)
        # Different t → different token
        tok3 = build_history_token(
            method="getHistoryTrend",
            key="https://item.jd.com/6290488.html",
            t="1610601399359",
        )
        self.assertNotEqual(tok, tok3)


class TestParseDatePrice(unittest.TestCase):
    def test_list_of_triples(self):
        raw = [
            [1508774400000, 629.0, ""],
            [1511452800000, 599.5, ""],
            [1514131200000, 610.0, ""],
        ]
        pts = parse_date_price(raw)
        self.assertEqual(len(pts), 3)
        self.assertEqual(pts[0].ts, 1508774400000)
        self.assertEqual(pts[0].price, 629.0)
        self.assertEqual(pts[1].price, 599.5)

    def test_json_string_array(self):
        s = '[[1609459200000,99.9,""],[1612137600000,89.0,""]]'
        pts = parse_date_price(s)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[1].price, 89.0)

    def test_hamflx_wrap_style(self):
        # Without outer brackets — parser wraps with []
        s = '[1609459200000,100.0,""],[1612137600000,90.0,""]'
        pts = parse_date_price(s)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0].price, 100.0)

    def test_seconds_to_ms(self):
        pts = parse_date_price([[1609459200, 50.0]])
        self.assertEqual(pts[0].ts, 1609459200000)

    def test_empty_and_junk(self):
        self.assertEqual(parse_date_price(None), [])
        self.assertEqual(parse_date_price(""), [])
        self.assertEqual(parse_date_price("not-json"), [])
        self.assertEqual(parse_date_price([["x", "y"]]), [])

    def test_series_from_points(self):
        pts = parse_date_price([[1_600_000_000_000, 10], [1_700_000_000_000, 30], [1_650_000_000_000, 20]])
        series = series_from_points(pts)
        assert series is not None
        self.assertEqual(series.count, 3)
        self.assertEqual(series.lowest, 10)
        self.assertEqual(series.highest, 30)
        self.assertEqual(series.avg, 20.0)
        self.assertEqual(series.source, "manmanbuy")
        # Chronological
        self.assertEqual([p.price for p in series.points], [10, 20, 30])


if __name__ == "__main__":
    unittest.main()
