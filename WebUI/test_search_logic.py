# -*- coding: utf-8 -*-
"""搜索归一化逻辑的回归测试。"""

import unittest
from unittest.mock import patch

from app import app, equivalent_part_number, parse_electronic_value, search_component


class SearchLogicTest(unittest.TestCase):
    def test_resistance_r_and_ohm_are_equivalent(self):
        self.assertEqual(parse_electronic_value("4R7"), parse_electronic_value("4.7Ω"))
        self.assertEqual(parse_electronic_value("68KΩ"), 68_000)

    def test_value_can_be_extracted_from_parameter_description(self):
        self.assertEqual(parse_electronic_value("68KΩ±1%"), 68_000)
        self.assertEqual(parse_electronic_value("68.1K / 0603"), 68_100)

    def test_uppercase_mega_is_not_milli(self):
        self.assertEqual(parse_electronic_value("1MΩ"), 1_000_000)
        self.assertEqual(parse_electronic_value("1mF"), 0.001)

    def test_different_ordering_suffixes_are_equivalent(self):
        self.assertTrue(equivalent_part_number("TPS82130SILR", "TPS82130SILT"))
        self.assertTrue(equivalent_part_number("TPS82130SILR", "TPS82130XYZ"))
        self.assertTrue(equivalent_part_number("ABC12345TRG", "ABC12345CT7"))

    def test_numeric_model_difference_is_not_equivalent(self):
        self.assertFalse(equivalent_part_number("TPS82130SILR", "TPS82131SILT"))
        self.assertFalse(equivalent_part_number("ABC12345TRG", "ABC12346TRG"))

    @patch("app.load_data")
    def test_silt_in_library_matches_silr_input(self, load_data):
        item = {"parameter": "TPS82130SILT", "footprint": "USIP-8"}
        load_data.return_value = {"A1234": item}
        self.assertEqual(search_component("TPS82130SILR"), ("A1234", item))

    @patch("app.load_data")
    def test_range_60k_to_70k_contains_68k(self, load_data):
        load_data.return_value = {
            "R62K": {"parameter": "62K"},
            "R68K": {"parameter": "68KΩ±1%"},
            "R75K": {"parameter": "75K"},
        }
        response = app.test_client().get("/search_range?min=60K&max=70K")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["part_number"] for row in response.get_json()["results"]], ["R62K", "R68K"])


if __name__ == "__main__":
    unittest.main()
