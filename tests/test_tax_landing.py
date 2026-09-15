"""Offline tests: compute_landing with tax, JD HTML/JSON price+tax, SQLite migrate."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.adapters.generic_html import extract_from_html
from app.adapters.jd import parse_jd_price_tax
from app.models import Product
from app.services import compute_landing


class TestComputeLandingTax(unittest.TestCase):
    def test_landing_adds_tax(self):
        self.assertEqual(compute_landing(100.0, coupon=10, full_reduction=5, tax_amount=20), 105.0)

    def test_landing_tax_zero_default(self):
        self.assertEqual(compute_landing(100.0, coupon=10, full_reduction=5), 85.0)

    def test_landing_floor_at_zero(self):
        self.assertEqual(compute_landing(10.0, coupon=50, full_reduction=0, tax_amount=5), 0.0)

    def test_landing_none_list(self):
        self.assertIsNone(compute_landing(None, tax_amount=10))

    def test_product_compute_landing(self):
        p = Product(
            name="t",
            platform="jd",
            url="https://item.jd.com/1.html",
            list_price=200.0,
            tax_amount=30.0,
            coupon_amount=20.0,
            full_reduction=10.0,
        )
        self.assertEqual(p.compute_landing(), 200.0)


class TestJdParsePriceTax(unittest.TestCase):
    def test_json_pprice_and_taxfee(self):
        html = """
        <html><body><script>
        var pageConfig = {"product":{"pPrice":"199.00","taxFee":"25.50","name":"进口奶粉"}};
        </script>
        <div>商品价：￥199.00</div>
        <div>预估税费：￥25.50</div>
        </body></html>
        """
        # pageConfig assignment may fail JSON load due to trailing; field regex + text should work
        r = parse_jd_price_tax(html)
        self.assertEqual(r["list_price"], 199.0)
        self.assertEqual(r["tax_amount"], 25.5)

    def test_field_regex_only(self):
        html = '{"skuId":"123","pPrice":"88.8","taxFee":"12.2","op":"99"}'
        r = parse_jd_price_tax(html)
        self.assertEqual(r["list_price"], 88.8)
        self.assertEqual(r["tax_amount"], 12.2)

    def test_allin_as_list_tax_zero(self):
        html = """
        <div>含税价：￥320.00</div>
        <script>var x = {"plusTaxPrice":"320.00"};</script>
        """
        r = parse_jd_price_tax(html)
        self.assertEqual(r["list_price"], 320.0)
        self.assertEqual(r["tax_amount"], 0.0)
        self.assertIn("含税", r["note"] or "")

    def test_prefer_split_over_allin(self):
        html = """
        商品价：￥280.00
        税费：￥40.00
        含税价：￥320.00
        """
        r = parse_jd_price_tax(html)
        self.assertEqual(r["list_price"], 280.0)
        self.assertEqual(r["tax_amount"], 40.0)

    def test_jdprice_realprice_keys(self):
        html = '"jdPrice":"156.00","realPrice":"156.00","taxation":"18.00"'
        r = parse_jd_price_tax(html)
        self.assertEqual(r["list_price"], 156.0)
        self.assertEqual(r["tax_amount"], 18.0)


class TestGenericHtmlTax(unittest.TestCase):
    def test_tax_not_picked_as_main_price(self):
        html = """
        <html><body>
          <div>￥299.00</div>
          <div>预估税费：￥35.00</div>
          <script>{"price":"299.00","taxFee":"35.00"}</script>
        </body></html>
        """
        r = extract_from_html(html)
        self.assertTrue(r.ok)
        self.assertEqual(r.price, 299.0)
        self.assertEqual(r.tax_amount, 35.0)

    def test_allin_pattern(self):
        html = "<html><body><div>含税价：￥450.00</div></body></html>"
        r = extract_from_html(html)
        self.assertTrue(r.ok)
        self.assertEqual(r.price, 450.0)
        self.assertTrue(r.tax_amount in (None, 0.0))


class TestSqliteTaxMigrate(unittest.TestCase):
    def test_migrate_adds_tax_columns(self):
        async def _run():
            with tempfile.TemporaryDirectory() as td:
                db_path = Path(td) / "old.db"
                # Simulate pre-tax schema
                eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
                async with eng.begin() as conn:
                    await conn.execute(
                        text(
                            "CREATE TABLE products ("
                            "id INTEGER PRIMARY KEY, name VARCHAR(256), platform VARCHAR(32), "
                            "url TEXT, list_price FLOAT, coupon_amount FLOAT, full_reduction FLOAT, "
                            "rebate_estimate FLOAT, landing_price FLOAT, enabled BOOLEAN)"
                        )
                    )
                    await conn.execute(
                        text(
                            "CREATE TABLE price_history ("
                            "id INTEGER PRIMARY KEY, product_id INTEGER, list_price FLOAT, "
                            "coupon_amount FLOAT, full_reduction FLOAT, rebate_estimate FLOAT, "
                            "landing_price FLOAT, source VARCHAR(32))"
                        )
                    )
                await eng.dispose()

                # Point config at this DB and run migrate
                from app import config as config_mod
                from app import db as db_mod
                from app.config import AppConfig

                config_mod._CONFIG = AppConfig(
                    databaseUrl=f"sqlite+aiosqlite:///{db_path}"
                )
                db_mod._engine = None
                db_mod._session_factory = None
                try:
                    await db_mod.init_db()
                    eng2 = db_mod.get_engine()
                    async with eng2.begin() as conn:
                        cols_p = {
                            row[1]
                            for row in (
                                await conn.execute(text("PRAGMA table_info(products)"))
                            ).fetchall()
                        }
                        cols_h = {
                            row[1]
                            for row in (
                                await conn.execute(text("PRAGMA table_info(price_history)"))
                            ).fetchall()
                        }
                    self.assertIn("tax_amount", cols_p)
                    self.assertIn("tax_amount", cols_h)
                finally:
                    config_mod._CONFIG = None
                    if db_mod._engine is not None:
                        await db_mod._engine.dispose()
                    db_mod._engine = None
                    db_mod._session_factory = None

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
