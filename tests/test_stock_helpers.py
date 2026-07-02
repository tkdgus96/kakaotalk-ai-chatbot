import unittest

from app.tools.stock import _extract_first_ticker, _extract_tickers, get_krx_code, is_symbol_like, normalize_stock_candidates


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

    def test_normalize_stock_candidates_natural_language_phrase(self):
        candidates = normalize_stock_candidates("meta's stock price")
        self.assertIn("META", candidates)

    def test_extract_first_ticker_prefers_krx_and_class_symbols(self):
        self.assertEqual(_extract_first_ticker("005380.KS"), "005380.KS")
        self.assertEqual(_extract_first_ticker("BRK.B"), "BRK-B")

    def test_extract_tickers_from_json_candidate_list(self):
        raw = '{"tickers":["329180.KS","267250.KS","BRK.B"]}'

        self.assertEqual(_extract_tickers(raw), ["329180.KS", "267250.KS", "BRK-B"])

    def test_is_symbol_like(self):
        self.assertTrue(is_symbol_like("PLTR"))
        self.assertTrue(is_symbol_like("005930.KS"))
        self.assertFalse(is_symbol_like("팔란티어"))
        self.assertFalse(is_symbol_like("martin"))

    def test_normalize_stock_candidates_palantir_alias(self):
        self.assertEqual(normalize_stock_candidates("팔란티어"), ["PLTR"])
        self.assertEqual(normalize_stock_candidates("palantir"), ["PLTR"])

    def test_normalize_stock_candidates_boeing_alias(self):
        self.assertEqual(normalize_stock_candidates("보잉"), ["BA"])


if __name__ == "__main__":
    unittest.main()
