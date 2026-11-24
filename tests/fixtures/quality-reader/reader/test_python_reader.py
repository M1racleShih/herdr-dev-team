import unittest


def read_amount(text):
    whole, fraction = text.split(".")
    return int(whole) * 100 + int(fraction)


class ReaderTest(unittest.TestCase):
    def test_decimal_fraction_is_preserved(self):
        self.assertEqual(read_amount("12.34"), 1234, "decimal fraction is preserved")
