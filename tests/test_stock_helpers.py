import unittest

from app.tools.stock import get_krx_code, normalize_stock_candidates


class StockHelperTests(unittest.TestCase):
    def test_normalize_stock_candidates_korean_alias(self):
        self.assertEqual(normalize_stock_candidates("삼성전자"), ["005930.KS"])

    def test_normalize_stock_candidates_krx_code_expands_suffixes(self):
        self.assertEqual(normalize_stock_candidates("005930"), ["005930", "005930.KS", "005930.KQ"])

    def test_get_krx_code_from_krx_symbol(self):
        self.assertEqual(get_krx_code("005930.KS"), "005930")
        self.assertEqual(get_krx_code("035720.KQ"), "035720")

    def test_get_krx_code_non_krx_symbol(self):
        self.assertIsNone(get_krx_code("META"))


if __name__ == "__main__":
    unittest.main()
