"""Unit tests: QQ「填价」command parsing contract (mirrors bot/src/commands.ts)."""

from __future__ import annotations

import re
import unittest

# Keep in sync with bot/src/commands.ts parseFillPriceCommand
_FILL_RE = re.compile(
    r"^(?:填价|改价|手动价)\s+#?(\d+)\s+(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?\s*$"
)


def parse_fill_price(text: str):
    m = _FILL_RE.match(text.strip())
    if not m:
        return None
    wid = int(m.group(1))
    a = float(m.group(2))
    if wid <= 0 or a < 0:
        return None
    if m.group(3) is not None:
        tax = float(m.group(3))
        if tax < 0:
            return None
        return {"id": wid, "mode": "list_tax", "list": a, "tax": tax}
    return {"id": wid, "mode": "landing", "landing": a}


class TestFillPriceParse(unittest.TestCase):
    def test_landing_only(self):
        self.assertEqual(
            parse_fill_price("填价 12 359.34"),
            {"id": 12, "mode": "landing", "landing": 359.34},
        )

    def test_landing_with_hash(self):
        self.assertEqual(
            parse_fill_price("填价 #3 100"),
            {"id": 3, "mode": "landing", "landing": 100.0},
        )

    def test_list_and_tax(self):
        self.assertEqual(
            parse_fill_price("填价 12 318 41.34"),
            {"id": 12, "mode": "list_tax", "list": 318.0, "tax": 41.34},
        )

    def test_aliases(self):
        self.assertEqual(parse_fill_price("改价 1 88")["mode"], "landing")
        self.assertEqual(parse_fill_price("手动价 2 10 1.5")["tax"], 1.5)

    def test_reject_garbage(self):
        self.assertIsNone(parse_fill_price("填价"))
        self.assertIsNone(parse_fill_price("填价 abc 1"))
        self.assertIsNone(parse_fill_price("帮助"))


if __name__ == "__main__":
    unittest.main()
