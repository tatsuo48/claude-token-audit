import json
from dataclasses import dataclass

from .model import Usage


@dataclass
class Cost:
    input: float = 0.0        # uncached prompt tokens at the input price
    cache_write: float = 0.0  # prompt tokens written to the cache (GPT-5.6+, 1.25x)
    cache_read: float = 0.0   # prompt tokens served from the cache (0.1x)
    output: float = 0.0       # output tokens including reasoning

    @property
    def total(self) -> float:
        return self.input + self.cache_write + self.cache_read + self.output

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(
            self.input + other.input,
            self.cache_write + other.cache_write,
            self.cache_read + other.cache_read,
            self.output + other.output,
        )


class Pricing:
    """Model price table. Prices in pricing.json are USD per 1M tokens."""

    def __init__(self, data: dict):
        self.mult = data["multipliers"]
        self.models = data["models"]
        self.default_model = data["default_model"]
        self.unknown_models = set()

    @classmethod
    def load(cls, path: str) -> "Pricing":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def resolve(self, model: str) -> str:
        """Exact match, else the longest key that is a dash-delimited prefix of `model`
        (so `gpt-5.5-2026-04-23` -> `gpt-5.5` but `gpt-5.7` never matches `gpt-5`)."""
        if model in self.models:
            return model
        prefixes = [k for k in self.models
                    if model.startswith(k) and len(model) > len(k) and model[len(k)] in "-_"]
        if prefixes:
            return max(prefixes, key=len)
        self.unknown_models.add(model)
        return self.default_model

    def rates(self, model: str) -> dict:
        m = self.models[self.resolve(model)]
        return {
            "input": m["input"] / 1e6,
            "output": m["output"] / 1e6,
            "read": m.get("cache_read", self.mult["cache_read"]),
            "write": m.get("cache_write", self.mult["cache_write"]),
        }

    def is_expensive(self, model: str, min_input_per_m: float = 4.0) -> bool:
        return self.models[self.resolve(model)]["input"] >= min_input_per_m

    def cost(self, model: str, u: Usage) -> Cost:
        r = self.rates(model)
        return Cost(
            input=u.uncached * r["input"],
            cache_write=u.cache_write * r["write"] * r["input"],
            cache_read=u.cached * r["read"] * r["input"],
            output=u.output * r["output"],
        )
