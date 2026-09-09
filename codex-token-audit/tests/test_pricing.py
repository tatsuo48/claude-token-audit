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

    def test_gpt55_cost_breakdown(self):
        u = Usage(input=12000, cached=10000, cache_write=1000, output=100, reasoning=40)
        c = self.p.cost("gpt-5.5", u)
        self.assertAlmostEqual(c.input, 1000 * 5e-6)
        self.assertAlmostEqual(c.cache_write, 1000 * 1.25 * 5e-6)
        self.assertAlmostEqual(c.cache_read, 10000 * 0.1 * 5e-6)
        self.assertAlmostEqual(c.output, 100 * 30e-6)
        self.assertAlmostEqual(c.total, c.input + c.cache_write + c.cache_read + c.output)

    def test_per_model_cache_read_override(self):
        c = self.p.cost("o3", Usage(input=10000, cached=10000))
        self.assertAlmostEqual(c.cache_read, 10000 * 0.25 * 2e-6)
        self.assertAlmostEqual(c.input, 0.0)

    def test_prefix_match_requires_dash_boundary(self):
        self.assertEqual(self.p.resolve("gpt-5.5-2026-04-23"), "gpt-5.5")
        self.assertEqual(self.p.resolve("gpt-5.1-codex-mini"), "gpt-5.1-codex-mini")
        self.assertEqual(self.p.resolve("gpt-5.6-sol"), "gpt-5.6-sol")
        self.assertEqual(self.p.resolve("gpt-5.7"), self.p.default_model)
        self.assertIn("gpt-5.7", self.p.unknown_models)

    def test_unknown_model_uses_default_and_is_recorded(self):
        self.assertEqual(self.p.resolve("mystery-9"), "gpt-5.5")
        self.assertIn("mystery-9", self.p.unknown_models)

    def test_expensive(self):
        self.assertTrue(self.p.is_expensive("gpt-5.5"))
        self.assertTrue(self.p.is_expensive("gpt-5.6-sol"))
        self.assertFalse(self.p.is_expensive("gpt-5.6-terra"))
        self.assertFalse(self.p.is_expensive("gpt-5.3-codex"))

    def test_cost_addition(self):
        s = Cost(1, 2, 3, 4) + Cost(1, 1, 1, 1)
        self.assertEqual((s.input, s.cache_write, s.cache_read, s.output), (2, 3, 4, 5))
        self.assertEqual(s.total, 14)


if __name__ == "__main__":
    unittest.main()
