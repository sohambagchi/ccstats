"""API list prices per model, for an estimated dollar cost of each API response.

This is what the tokens would cost at Anthropic's first-party API rates (a
subscription bills differently). Cache writes are priced from the input rate:
1.25x for the 5-minute TTL, 2x for the 1-hour one. Fast mode, web search fees
and partner (Bedrock/Vertex) pricing are not modelled.

Bump VERSION whenever PRICES changes: the store re-prices every stored message
and rebuilds the session rollups on its next refresh, without re-parsing.
"""
from __future__ import annotations

import re

VERSION = "2026-11-09"

# $ per million tokens: (input, output, cache read). Keys match model ids by prefix,
# after any date suffix or "[1m]" tag is dropped; the longest matching key wins.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-mythos-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-mythos-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-opus-4-5": (5.0, 25.0, 0.50),
    "claude-opus-4-1": (15.0, 75.0, 1.50),
    "claude-opus-4": (15.0, 75.0, 1.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-sonnet-4-5": (3.0, 15.0, 0.30),
    "claude-sonnet-4": (3.0, 15.0, 0.30),
    "claude-3-7-sonnet": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
    "claude-3-5-haiku": (0.80, 4.0, 0.08),
    "claude-3-haiku": (0.25, 1.25, 0.03),
}

# Long-prompt tiers: above this many prompt tokens (input + cache read + cache write)
# the response is billed at (input-side multiplier, output multiplier).
LONG_PROMPT: dict[str, tuple[int, float, float]] = {
    "claude-haiku-5-5": (100_000, 5.0, 5.0),
    "claude-sonnet-4-5": (200_000, 2.0, 1.5),
    "claude-sonnet-4": (200_000, 2.0, 1.5),
}

_SUFFIX = re.compile(r"(\[.*\]|-\d{8}|@\d{8})$")


def _key(model: str | None, table: dict) -> str | None:
    if not model:
        return None
    base = _SUFFIX.sub("", model.strip().lower())
    base = base.removeprefix("anthropic.")
    best = None
    for k in table:
        if (base == k or base.startswith(k + "-")) and (best is None or len(k) > len(best)):
            best = k
    return best


def known(model: str | None) -> bool:
    return _key(model, PRICES) is not None


def usd(model: str | None, input: int = 0, output: int = 0, cache_read: int = 0,
        cache_write: int = 0, cache_write_1h: int = 0) -> float:
    """Estimated cost of one API response; 0 for models without a price."""
    k = _key(model, PRICES)
    if k is None:
        return 0.0
    p_in, p_out, p_read = PRICES[k]
    m_in = m_out = 1.0
    lk = _key(model, LONG_PROMPT)
    if lk is not None:
        limit, a, b = LONG_PROMPT[lk]
        if input + cache_read + cache_write > limit:
            m_in, m_out = a, b
    cw_5m = max(0, cache_write - cache_write_1h)
    return (m_in * (input * p_in + cache_read * p_read + cw_5m * p_in * 1.25 + cache_write_1h * p_in * 2.0)
            + m_out * output * p_out) / 1e6
