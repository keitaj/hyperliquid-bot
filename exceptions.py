"""Custom exception hierarchy for the Hyperliquid trading bot.

Classifies API/network errors so that callers can distinguish between
transient failures (worth retrying) and permanent failures (fail fast).

Hierarchy
---------
HyperliquidBotError
├── TransientError          — retryable
│   ├── RateLimitError      — 429 / rate-limited
│   └── NetworkError        — timeout, connection reset, DNS failure
├── DataError               — unexpected response structure or parsing failure
└── ConfigurationError      — invalid parameters, signing errors
"""


class HyperliquidBotError(Exception):
    """Base exception for all bot-classified errors."""


class TransientError(HyperliquidBotError):
    """Retryable errors — the same request may succeed later."""


class RateLimitError(TransientError):
    """API rate limit hit (HTTP 429 or equivalent)."""


class NetworkError(TransientError):
    """Connection timeout, reset, DNS failure, etc."""


class DataError(HyperliquidBotError):
    """Unexpected data from the API — missing keys, wrong types, parse failures."""


class ConfigurationError(HyperliquidBotError):
    """Invalid parameters or signing errors — will never succeed without a code change."""
