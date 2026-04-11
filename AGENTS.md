# AGENTS.md

<!-- version: 1.0.0 -->

Automated trading bot for Hyperliquid DEX with HIP-3 multi-DEX support.
Python 3.11+, no web framework — runs as a standalone process.

## Principles

- **Safety first** — this bot handles real money. Be conservative with changes to order logic, risk management, and position sizing.
- Follow existing patterns in surrounding code
- Write tests for new functionality
- Keep changes focused — one feature or fix per PR
- Never commit secrets (`.env`, API keys, private keys)

## Commands

### Setup

```bash
pip install ".[dev]"
```

Environment variables are loaded from `.env` (never commit this file).

### Run

```bash
python bot.py --strategy market_making --coin BTC   # Start the bot
python check_balance.py                              # Check account balance
```

### Test

```bash
python -m pytest tests/ -v              # All tests
python -m pytest tests/test_foo.py -v   # Specific file
python -m pytest tests/ -v -k "test_name"  # Specific test by name
```

All tests are pure unit tests using mocks — no network or API keys required.
Always run the full test suite before committing.

### Lint

```bash
flake8 . --max-line-length=120 --exclude=.git,__pycache__,.env
```

Run lint before committing. CI enforces both lint and tests.

## Code Conventions

- Follow PEP 8; max line length 120
- Use f-strings for string formatting (not `%` or `.format()`)
- Add type hints to function signatures
- Do not use `any` type — use proper typing
- Mock external dependencies (SDK, network) in tests; never make real API calls
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `refactor:`, etc.)

## PR Instructions

- PR titles and descriptions must be in English
- Keep PRs focused — one feature or fix per PR
- Ensure CI passes (lint + tests) before requesting review
- Update `README.md` if you add new CLI flags or strategies

## Architecture

| File / Directory | Purpose |
|---|---|
| `bot.py` | Entry point — argparse, strategy dispatch |
| `config.py` | Loads `.env`, provides `get()` with defaults |
| `market_data.py` | REST/WS wrappers for Hyperliquid market data |
| `order_manager.py` | Order placement, cancellation, bulk operations |
| `position_closer.py` | Graceful position unwinding |
| `risk_manager.py` | Per-trade and portfolio risk checks |
| `circuit_breaker.py` | Halts trading on anomalous conditions |
| `rate_limiter.py` | API rate-limit management with retry |
| `exceptions.py` | Custom exception hierarchy |
| `strategies/base_strategy.py` | Abstract base — `generate_signals()`, `calculate_position_size()` |
| `strategies/market_making_strategy.py` | Market making with BBO tracking |
| `strategies/mm_position_closer.py` | MM-specific position closer |
| `strategies/mm_order_tracker.py` | MM order fill tracking |
| `strategies/*_strategy.py` | RSI, MACD, Bollinger, Breakout, Grid, Simple MA |
| `hip3/` | HIP-3 multi-DEX support (registry, orders, market data) |
| `validation/` | Strategy and margin validators |
| `tests/` | Unit tests (pytest, all mocked) |

## Adding a New Strategy

1. Create `strategies/your_strategy.py` extending `BaseStrategy`
2. Implement `generate_signals()` and `calculate_position_size()`
3. Read all parameters from `config.get('key', default)` — no hardcoded values
4. Register in `strategies/__init__.py` and `bot.py` (strategy_map + default_configs + argparse)
5. Add tests in `tests/`
6. Document in `README.md`

## Key Notes

- **No hardcoded values** — all strategy parameters must come from config with sensible defaults
- **Circuit breaker** — halts all trading activity on anomalous conditions; do not bypass or weaken
- **Rate limiter** — wraps all API calls with retry logic; always use it, never call the SDK directly
- **Exception hierarchy** — use custom exceptions from `exceptions.py` for classifiable error handling
- **HIP-3** — multi-DEX support is modular under `hip3/`; changes there do not affect core strategies
- **Dependencies** — do not introduce new dependencies without discussion
