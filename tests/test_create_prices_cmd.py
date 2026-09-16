"""Unit tests: QQ「监控」current+target / trailing parse (mirrors bot/src/commands.ts)."""

from __future__ import annotations

import re
import unittest

# Product units — digits glued to these are NOT prices (奶粉2段 → not ¥2)
_PRODUCT_UNIT_RE = re.compile(
    r"^(?:段|罐|盒|袋|瓶|件|岁|月|抽|片|斤|两|升|克|个|只|双|条|包|箱|桶|支|台|部|辆|kg|g|ml|L|人份)",
    re.I,
)
_DEFAULT_MIN_PLAUSIBLE = 10.0


def _has_decimal(n: float) -> bool:
    return abs(n - round(n)) > 1e-9


def is_acceptable_create_price(
    n: float,
    *,
    labeled: bool = False,
    followed_by_unit: bool = False,
    min_plausible: float = _DEFAULT_MIN_PLAUSIBLE,
) -> bool:
    if n < 0:
        return False
    if followed_by_unit:
        return False
    if labeled:
        return n > 0
    if _has_decimal(n):
        return n > 0
    return n >= min_plausible


def _scan_price_numbers(text: str, min_plausible: float) -> tuple[list[float], list[float]]:
    accepted: list[float] = []
    rejected: list[float] = []
    seen: set[str] = set()

    def push(value: float, labeled: bool, followed_by_unit: bool) -> None:
        key = f"{value}|{labeled}|{followed_by_unit}"
        if key in seen:
            return
        seen.add(key)
        if is_acceptable_create_price(
            value,
            labeled=labeled,
            followed_by_unit=followed_by_unit,
            min_plausible=min_plausible,
        ):
            accepted.append(value)
        elif followed_by_unit or (not labeled and value < min_plausible):
            rejected.append(value)

    labeled_re = re.compile(
        r"(?:到手价?|现价|价格|填价|售价|促销价)\s*[：:=\s]*[￥¥]?\s*(\d+(?:\.\d+)?)"
        r"|(?:[￥¥]\s*(\d+(?:\.\d+)?))"
        r"|(\d+(?:\.\d+)?)\s*元"
    )
    for m in labeled_re.finditer(text):
        raw = m.group(1) or m.group(2) or m.group(3)
        if raw is not None:
            push(float(raw), True, False)

    for m in re.finditer(r"(\d+(?:\.\d+)?)", text):
        n = float(m.group(1))
        after = text[m.end() :]
        before = text[max(0, m.start() - 1) : m.start()]
        if before == "." or re.match(r"\.\d", after):
            continue
        rest = re.sub(r"^\s*", "", after)
        glued = bool(_PRODUCT_UNIT_RE.match(rest))
        push(n, False, glued)

    return accepted, rejected


def extract_create_prices(
    text: str, url: str | None = None, *, min_plausible: float = _DEFAULT_MIN_PLAUSIBLE
) -> dict:
    """Mirror bot extractCreatePrices (with unit / plausible filters)."""
    out: dict = {}
    target_kw = None
    kw = re.search(r"目标价?\s*[：:=\s]*(\d+(?:\.\d+)?)", text)
    if kw:
        target_kw = float(kw.group(1))

    nums: list[float] = []
    rejected = None
    if url and url in text:
        after = text[text.index(url) + len(url) :]
        after = re.sub(r"目标价?\s*[：:=\s]*\d+(?:\.\d+)?", " ", after)
        accepted, rej = _scan_price_numbers(after, min_plausible)
        for n in accepted:
            if n not in nums:
                nums.append(n)
            if len(nums) >= 2:
                break
        if rej:
            rejected = rej[0]
    if not nums:
        m = re.match(
            r"(?:监控|盯价|加监控)\s+\S+(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?\s*$",
            text.strip(),
        )
        if m:
            for g in (m.group(1), m.group(2)):
                if not g:
                    continue
                n = float(g)
                if not is_acceptable_create_price(n, min_plausible=min_plausible):
                    if n < min_plausible:
                        rejected = rejected if rejected is not None else n
                    continue
                nums.append(n)

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
    if rejected is not None and "current" not in out and "trailing" not in out:
        out["rejected"] = rejected
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
    SHARE_URL = "https://3.jd.hk/abc123"

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

    def test_formula_stage_not_price(self):
        """Bug: 「奶粉2段」 must NOT yield current/trailing=2."""
        text = (
            f"监控 【京东】{self.SHARE_URL} "
            f"「【询客服领券】a2紫白金奶粉2段」"
        )
        p = extract_create_prices(text, self.SHARE_URL)
        self.assertNotEqual(p.get("trailing"), 2)
        self.assertNotEqual(p.get("current"), 2)
        self.assertNotIn("trailing", p)
        self.assertNotIn("current", p)
        self.assertEqual(p.get("rejected"), 2.0)

    def test_trailing_decimal_ok(self):
        p = extract_create_prices(f"{self.URL} 359.34", self.URL)
        self.assertEqual(p.get("trailing"), 359.34)

    def test_trailing_yuan_label_ok(self):
        p = extract_create_prices(f"{self.URL} 359元", self.URL)
        self.assertEqual(p.get("trailing"), 359.0)

    def test_stage_then_trailing_price(self):
        """「2段 300」 → only 300 as trailing."""
        p = extract_create_prices(f"{self.URL} 2段 300", self.URL)
        self.assertEqual(p.get("trailing"), 300.0)
        self.assertNotEqual(p.get("current"), 2)

    def test_currency_labeled_small_ok(self):
        p = extract_create_prices(f"{self.URL} ￥9.9", self.URL)
        self.assertEqual(p.get("trailing"), 9.9)

    def test_lone_integer_below_min_rejected(self):
        p = extract_create_prices(f"{self.URL} 2", self.URL)
        self.assertNotIn("trailing", p)
        self.assertEqual(p.get("rejected"), 2.0)

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
