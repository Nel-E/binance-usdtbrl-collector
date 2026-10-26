"""Offline tests: no API credentials or network calls required."""
import unittest
import collector

class TestCollector(unittest.TestCase):
    def test_ema(self):
        self.assertIsNone(collector.simple_ema([1, 2], 3))
        self.assertAlmostEqual(collector.simple_ema([1, 2, 3], 3), 2)

    def test_depth_bands(self):
        book = {"lastUpdateId": 123, "bids": [["5.49", "100"], ["5.47", "30"]],
                "asks": [["5.50", "90"], ["5.52", "10"]]}
        result = collector.parse_depth(book, 5.495)
        self.assertEqual(result["best_bid"], 5.49)
        self.assertEqual(result["best_ask"], 5.50)
        self.assertGreater(result["depth_bands_midpoint"]["0.5%"]["bid_share_pct"], 0)
        self.assertEqual(result["largest_visible_bid_walls"][0]["price_brl"], 5.49)

    def test_closed_candle_distinguished_from_open(self):
        def candle(open_ms, close_ms, price):
            return [open_ms, str(price), str(price), str(price), str(price), "10", close_ms, "50", 12, "6", "30", "0"]
        rows = [candle(1000, 2000, 5.4), candle(2001, 3000, 5.5)]
        result = collector.parse_klines(rows, server_time_ms=2500, period="15m")
        self.assertEqual(result["latest_closed"]["close"], 5.4)
        self.assertEqual(result["current_incomplete"]["close"], 5.5)

    def test_invalid_crossed_book(self):
        with self.assertRaises(ValueError):
            collector.parse_depth({"lastUpdateId":1,"bids":[["5.60","1"]], "asks":[["5.50","1"]]},5.55)

if __name__ == "__main__":
    unittest.main()
