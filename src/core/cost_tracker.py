"""Token usage and cost tracking.

Ported from claude-code-main's ``src/utils/modelCost.ts`` and
``src/cost-tracker.ts``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Pricing per million tokens ($/MTok)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _PricingTier:
    input: float
    output: float
    cache_write: float
    cache_read: float


# Named tiers matching claude-code-main modelCost.ts
_TIER_3_15 = _PricingTier(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30)
_TIER_15_75 = _PricingTier(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50)
_TIER_5_25 = _PricingTier(input=5.0, output=25.0, cache_write=6.25, cache_read=0.50)
_TIER_HAIKU_35 = _PricingTier(input=0.80, output=4.0, cache_write=1.0, cache_read=0.08)
_TIER_HAIKU_45 = _PricingTier(input=1.0, output=5.0, cache_write=1.25, cache_read=0.10)

_TIER_30_150 = _PricingTier(input=30.0, output=150.0, cache_write=37.5, cache_read=3.0)

# Model prefix/substring -> tier.  Order matters: first match wins.
_MODEL_PRICING: list[tuple[str, _PricingTier]] = [
    ("claude-3-5-haiku", _TIER_HAIKU_35),
    ("claude-haiku-4-5", _TIER_HAIKU_45),
    ("claude-opus-4-5", _TIER_5_25),
    ("claude-opus-4-1", _TIER_15_75),
    ("claude-opus-4", _TIER_15_75),
    ("claude-sonnet", _TIER_3_15),
    ("claude-3-5-sonnet", _TIER_3_15),
    ("claude-3-7-sonnet", _TIER_3_15),
]

_DEFAULT_TIER = _TIER_3_15  # fallback for Claude-family / unknown legacy names


def _tier_for_model(model: str, usage: dict | None = None) -> _PricingTier | None:
    model_lower = model.lower()
    
    if "claude-opus-4-6" in model_lower:
        if usage and usage.get("speed") == "fast":
            return _TIER_30_150
        return _TIER_5_25
        
    for prefix, tier in _MODEL_PRICING:
        if prefix in model_lower:
            return tier
    if model_lower.startswith(("gpt-", "o1", "o3", "o4")):
        return None
    return _DEFAULT_TIER


def _is_known_model(model: str) -> bool:
    model_lower = model.lower()
    if "claude-opus-4-6" in model_lower:
        return True
    for prefix, _ in _MODEL_PRICING:
        if prefix in model_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Usage data
# ---------------------------------------------------------------------------

@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    api_duration_s: float = 0.0
    pricing_known: bool = True


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_tokens(n: int) -> str:
    """Format token count with k/m suffixes like the official CLI."""
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}m" if v != int(v) else f"{int(v)}m"
    if n >= 1_000:
        v = n / 1_000
        return f"{v:.1f}k" if v != int(v) else f"{int(v)}k"
    return str(n)


def _fmt_duration(seconds: float) -> str:
    """Format seconds as 'Xh Ym Zs', 'Ym Zs', or 'Xs'."""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------

class CostTracker:
    """Accumulates token usage and cost across API calls."""

    def __init__(self) -> None:
        self._total_cost_usd: float = 0.0
        self._total_api_duration_s: float = 0.0
        self._model_usage: dict[str, ModelUsage] = {}
        self._wall_start: float = time.monotonic()
        self._lines_added: int = 0
        self._lines_removed: int = 0
        self._last_input_tokens: int = 0

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    @property
    def last_input_tokens(self) -> int:
        """The input_tokens from the most recent API call (reflects context size)."""
        return self._last_input_tokens

    @staticmethod
    def calculate_cost(model: str, usage: dict) -> float:
        """Return cost in USD for a single API call."""
        tier = _tier_for_model(model, usage)
        if tier is None:
            return 0.0
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)

        # Anthropic API: input_tokens already excludes cached tokens.
        # cache_read and cache_write are billed separately at their own rates.
        cost = (
            inp * tier.input
            + out * tier.output
            + cache_read * tier.cache_read
            + cache_write * tier.cache_write
        ) / 1_000_000
        return cost

    def add_usage(self, model: str, usage: dict, api_duration_s: float = 0.0) -> float:
        """Record token counts and return cost for this call."""
        cost = self.calculate_cost(model, usage)
        self._total_cost_usd += cost
        self._total_api_duration_s += api_duration_s
        self._last_input_tokens = usage.get("input_tokens", 0)

        mu = self._model_usage.setdefault(model, ModelUsage())
        mu.input_tokens += usage.get("input_tokens", 0)
        mu.output_tokens += usage.get("output_tokens", 0)
        mu.cache_read_input_tokens += usage.get("cache_read_input_tokens", 0)
        mu.cache_creation_input_tokens += usage.get("cache_creation_input_tokens", 0)
        mu.cost_usd += cost
        mu.api_duration_s += api_duration_s
        if not _is_known_model(model):
            mu.pricing_known = False
        return cost

    def add_lines_changed(self, added: int, removed: int) -> None:
        """Record code changes (lines added/removed) from Edit/Write tools."""
        self._lines_added += added
        self._lines_removed += removed

    def format_cost(self) -> str:
        """Human-readable cost summary matching official Claude Code format."""
        if not self._model_usage:
            return "No API usage recorded."

        wall_s = time.monotonic() - self._wall_start
        unknown_pricing = any(not mu.pricing_known for mu in self._model_usage.values())
        lines: list[str] = []
        lines.append(f"Total cost:            ${self._total_cost_usd:.2f}")
        if unknown_pricing:
            lines.append("Pricing note:          Costs may be inaccurate due to usage of unknown models")
        lines.append(f"Total duration (API):  {_fmt_duration(self._total_api_duration_s)}")
        lines.append(f"Total duration (wall): {_fmt_duration(wall_s)}")
        la = self._lines_added
        lr = self._lines_removed
        lines.append(
            f"Total code changes:    {la} {'line' if la == 1 else 'lines'} added, "
            f"{lr} {'line' if lr == 1 else 'lines'} removed"
        )

        # Per-model usage — one compact line each
        lines.append("Usage by model:")

        # Right-align model names
        max_name = max(len(m) for m in self._model_usage)
        for model, mu in sorted(self._model_usage.items()):
            parts: list[str] = []
            parts.append(f"{_fmt_tokens(mu.input_tokens)} input")
            parts.append(f"{_fmt_tokens(mu.output_tokens)} output")
            if mu.cache_read_input_tokens:
                parts.append(f"{_fmt_tokens(mu.cache_read_input_tokens)} cache read")
            if mu.cache_creation_input_tokens:
                parts.append(f"{_fmt_tokens(mu.cache_creation_input_tokens)} cache write")
            detail = ", ".join(parts)
            if not mu.pricing_known:
                detail += ", pricing unavailable"
            name_pad = model.rjust(max_name)
            lines.append(f"  {name_pad}:  {detail} (${mu.cost_usd:.4f})")

        return "\n".join(lines)
