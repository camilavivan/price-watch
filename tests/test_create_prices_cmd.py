"""Unit tests: QQ「监控」current+target / trailing parse (mirrors bot/src/commands.ts)."""

from __future__ import annotations

import re
import unittest


def extract_create_prices(text: str, url: str | None = None) -> dict:
    """Mirror bot extractCreatePrices."""
    out: dict = {}
    target_kw = None
    kw = re.search(r"目标价?\s*[：:=\s]*(\d+(?:\.\d+)?)", text)
    if kw:
        target_kw = float(kw.group(1))

    nums: list[float] = []
    if url and url in text:
        after = text[text.index(url) + len(url) :]
        stripped = re.sub(r"目标价?\s*[：:=\s]*\d+(?:\.\d+)?", " ", after)
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*元?", stripped):
            n = float(m.group(1))
            if n >= 0:
                nums.append(n)
            if len(nums) >= 2:
                break
    if not nums:
        m = re.match(
            r"(?:监控|盯价|加监控)\s+\S+(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?\s*$",
            text.strip(),
        )
        if m:
            if m.group(1):
                nums.append(float(m.group(1)))
            if m.group(2):
                nums.append(float(m.group(2)))

    if len(nums) >= 2:
        out["current"] = nums[0]
        out["target"] = nums[1]
    elif len(nums) == 1:
        if target_kw is not None and abs(nums[0] - target_kw) < 1e-9:
            out["target"] = target_kw
        elif target_kw is not None:
            out["current"] = nums[0]
            out["target"] = target_kw
        else:
            out["trailing"] = nums[0]
    elif target_kw is not None:
        out["target"] = target_kw
    return out


def parse_target_command(text: str):
    m = re.match(r"^目标\s+#?(\d+)\s+(\d+(?:\.\d+)?)\s*元?\s*$", text.strip())
    if not m:
        return None
    wid, target = int(m.group(1)), float(m.group(2))
    if wid <= 0 or target < 0:
        return None
    return {"id": wid, "target": target}


def resolve_trailing(trailing, landing_present: bool):
    """Server rule: trailing → current if no landing else target."""
    if trailing is None:
        return None, None
    if landing_present:
        return None, trailing
    return trailing, None


class TestCreatePricesParse(unittest.TestCase):
    URL = "https://item.jd.com/100012043978.html"

    def test_current_and_target(self):
        p = extract_create_prices(f"监控 {self.URL} 359.34 300", self.URL)
        self.assertEqual(p["current"], 359.34)
        self.assertEqual(p["target"], 300.0)
        self.assertNotIn("trailing", p)

    def test_single_trailing_as_current_candidate(self):
        p = extract_create_prices(f"监控 {self.URL} 359.34", self.URL)
        self.assertEqual(p.get("trailing"), 359.34)
        self.assertNotIn("current", p)
        self.assertNotIn("target", p)

    def test_share_paste_trailing(self):
        p = extract_create_prices(f"{self.URL} 359", self.URL)
        self.assertEqual(p.get("trailing"), 359.0)

    def test_target_keyword_only(self):
        p = extract_create_prices(f"监控 {self.URL} 目标价 300", self.URL)
        self.assertEqual(p.get("target"), 300.0)
        self.assertNotIn("trailing", p)

    def test_current_plus_target_keyword(self):
        p = extract_create_prices(f"监控 {self.URL} 359 目标价 300", self.URL)
        self.assertEqual(p.get("current"), 359.0)
        self.assertEqual(p.get("target"), 300.0)

    def test_trailing_resolve_no_auto(self):
        cur, tgt = resolve_trailing(359.34, landing_present=False)
        self.assertEqual(cur, 359.34)
        self.assertIsNone(tgt)

    def test_trailing_resolve_with_auto(self):
        cur, tgt = resolve_trailing(300.0, landing_present=True)
        self.assertIsNone(cur)
        self.assertEqual(tgt, 300.0)


class TestTargetCommand(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(parse_target_command("目标 1 299"), {"id": 1, "target": 299.0})

    def test_hash(self):
        self.assertEqual(parse_target_command("目标 #12 99.5"), {"id": 12, "target": 99.5})

    def test_reject(self):
        self.assertIsNone(parse_target_command("目标"))
        self.assertIsNone(parse_target_command("目标 abc 1"))
        self.assertIsNone(parse_target_command("填价 1 100"))


class TestRiskBackoffHelper(unittest.TestCase):
    def test_risk_markers(self):
        from app.services import is_risk_block_error

        self.assertTrue(is_risk_block_error("京东反爬/风控拦截，服务器无法自动取价"))
        self.assertTrue(is_risk_block_error("页面无内嵌价格（需接口/手动）"))
        self.assertFalse(is_risk_block_error(None))
        self.assertFalse(is_risk_block_error("timeout"))


if __name__ == "__main__":
    unittest.main()
