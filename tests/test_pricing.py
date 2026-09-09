import os
import unittest

from tokenaudit.model import Usage
from tokenaudit.pricing import Cost, Pricing

PRICING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "token-audit", "scripts", "pricing.json",
)


class PricingTest(unittest.TestCase):
    def setUp(self):
        self.p = Pricing.load(PRICING_PATH)

    def test_opus5_cost_breakdown(self):
        u = Usage(input=1000, cache_5m=1000, cache_1h=1000, cache_read=10000, output=100)
        c = self.p.cost("claude-opus-5", u)
        self.assertAlmostEqual(c.input, 0.005)
        self.assertAlmostEqual(c.cache_write, (1000 * 1.25 + 1000 * 2.0) * 5e-6)
        self.assertAlmostEqual(c.cache_read, 10000 * 0.1 * 5e-6)
        self.assertAlmostEqual(c.output, 100 * 25e-6)
        self.assertAlmostEqual(c.total, 0.005 + 0.01625 + 0.005 + 0.0025)

    def test_fable51_cache_read_override(self):
        c = self.p.cost("claude-fable-5-1", Usage(cache_read=10000))
        self.assertAlmostEqual(c.cache_read, 10000 * 0.025 * 10e-6)

    def test_prefix_match_resolves_dated_model(self):
        self.assertEqual(self.p.resolve("claude-opus-4-6-20260101"), "claude-opus-4-6")
        self.assertEqual(self.p.resolve("claude-fable-5-1"), "claude-fable-5-1")

    def test_unknown_model_uses_default_and_is_recorded(self):
        self.assertEqual(self.p.resolve("claude-mystery-9"), "claude-opus-5")
        self.assertIn("claude-mystery-9", self.p.unknown_models)

    def test_cost_addition(self):
        a = Cost(1, 2, 3, 4)
        b = Cost(1, 1, 1, 1)
        s = a + b
        self.assertEqual((s.input, s.cache_write, s.cache_read, s.output), (2, 3, 4, 5))
        self.assertEqual(s.total, 14)


if __name__ == "__main__":
    unittest.main()
