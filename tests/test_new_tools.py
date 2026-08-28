import unittest

from app.tools.calculator import calculate
from app.tools.currency import _norm
from app.tools.datetime_tool import date_calculate
from app.tools.units import convert_unit


class CalculatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_basic_arithmetic(self):
        self.assertIn("= 1500", await calculate.ainvoke({"expression": "(2500+500)/2"}))
        self.assertIn("= 1024", await calculate.ainvoke({"expression": "2**10"}))
        self.assertIn("= 12", await calculate.ainvoke({"expression": "sqrt(144)"}))
        self.assertIn("= 450", await calculate.ainvoke({"expression": "0.15*3000"}))

    async def test_odd_sum(self):
        # 1..100 홀수 합 = 2500
        self.assertIn("= 2500", await calculate.ainvoke({"expression": "50**2"}))

    async def test_rejects_unsafe(self):
        out = await calculate.ainvoke({"expression": "__import__('os').system('ls')"})
        self.assertIn("계산할 수 없는", out)
        out2 = await calculate.ainvoke({"expression": "open('x')"})
        self.assertIn("계산할 수 없는", out2)


class CurrencyNormTests(unittest.TestCase):
    def test_alias_and_code(self):
        self.assertEqual(_norm("달러"), "USD")
        self.assertEqual(_norm("엔"), "JPY")
        self.assertEqual(_norm("원"), "KRW")
        self.assertEqual(_norm("usd"), "USD")
        self.assertEqual(_norm("EUR"), "EUR")


class UnitConvertTests(unittest.IsolatedAsyncioTestCase):
    async def test_length_weight_temp(self):
        self.assertIn("6.214", await convert_unit.ainvoke({"value": 10, "from_unit": "km", "to_unit": "mile"}))
        self.assertIn("11.02", await convert_unit.ainvoke({"value": 5, "from_unit": "kg", "to_unit": "lb"}))
        self.assertIn("212", await convert_unit.ainvoke({"value": 100, "from_unit": "섭씨", "to_unit": "화씨"}))

    async def test_incompatible_dimensions(self):
        out = await convert_unit.ainvoke({"value": 1, "from_unit": "kg", "to_unit": "km"})
        self.assertIn("다른 종류", out)


class DateCalcTests(unittest.IsolatedAsyncioTestCase):
    async def test_weekday_and_diff_and_add(self):
        self.assertIn("화요일", await date_calculate.ainvoke({"operation": "weekday", "date": "2026-09-01"}))
        self.assertIn("30일", await date_calculate.ainvoke(
            {"operation": "diff", "date": "2026-09-01", "date2": "2026-10-01"}))
        self.assertIn("2026-09-08", await date_calculate.ainvoke(
            {"operation": "add", "date": "2026-09-01", "days": 7}))


if __name__ == "__main__":
    unittest.main()
