import unittest

from app.boss.utils.price import PriceParseError, parse_price_to_mesos


class TestPriceParser(unittest.TestCase):
    def test_eok(self):
        self.assertEqual(parse_price_to_mesos("84억"), 8_400_000_000)

    def test_decimal_eok(self):
        self.assertEqual(parse_price_to_mesos("12.5억"), 1_250_000_000)

    def test_man(self):
        self.assertEqual(parse_price_to_mesos("8500만"), 85_000_000)

    def test_eok_man(self):
        self.assertEqual(parse_price_to_mesos("1억5000만"), 150_000_000)

    def test_plain_number(self):
        self.assertEqual(parse_price_to_mesos("100000000"), 100_000_000)

    def test_invalid(self):
        with self.assertRaises(PriceParseError):
            parse_price_to_mesos("abc")
