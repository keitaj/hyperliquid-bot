"""Command-line interface definition for the Hyperliquid trading bot.

Owns every argparse flag. Extracted verbatim from the ``__main__`` block
of ``bot.py`` so the entry point stays focused on wiring and startup;
flag names, dests, defaults and help text are unchanged.

``build_parser`` is a pure function: importing this module or calling it
has no side effects (no env reads, no Config mutation), which also makes
the real parser directly testable.
"""
import argparse


def build_parser() -> argparse.ArgumentParser:
    """Return the fully-configured ArgumentParser for ``bot.py``."""
    parser = argparse.ArgumentParser(description='Hyperliquid Trading Bot')
    parser.add_argument(
        '--strategy',
        type=str,
        default='simple_ma',
        choices=['simple_ma', 'rsi', 'bollinger_bands', 'macd', 'grid_trading', 'breakout', 'market_making'],
        help='Trading strategy to use'
    )
    parser.add_argument(
        '--coins',
        type=str,
        nargs='+',
        default=['BTC', 'ETH', 'SOL'],
        help='Coins to trade on standard Hyperliquid (e.g. BTC ETH SOL)'
    )
    parser.add_argument(
        '--dex',
        type=str,
        nargs='+',
        default=None,
        help=(
            'HIP-3 DEX names to trade on (e.g. xyz flx). '
            'Overrides TRADING_DEXES env var. '
            'Use XYZ_COINS / FLX_COINS env vars to set per-DEX coin lists.'
        )
    )
    parser.add_argument(
        '--no-hl',
        action='store_true',
        default=False,
        help='Disable trading on standard Hyperliquid perps (trade only HIP-3 DEXes)'
    )
    parser.add_argument(
        '--enable-ws',
        action='store_true',
        default=False,
        help='Enable WebSocket feed for real-time L2 book updates (reduces REST API calls)'
    )

    # Common strategy parameters
    parser.add_argument('--position-size-usd', type=float, help='Position size in USD')
    parser.add_argument('--max-positions', type=int, help='Maximum number of positions')
    parser.add_argument('--take-profit-percent', type=float, help='Take profit percentage')
    parser.add_argument('--stop-loss-percent', type=float, help='Stop loss percentage')
    parser.add_argument('--candle-interval', type=str, help='Candle interval (e.g. 1m, 5m, 15m, 1h)')
    parser.add_argument('--market-order-slippage', type=float, help='Market order slippage (default: 0.01 = 1%%)')
    parser.add_argument('--main-loop-interval', type=float, help='Main loop sleep interval in seconds (default: 10)')
    parser.add_argument('--account-cap-pct', type=float,
                        help='Max position as %% of account for sizing (grid_trading/market_making)')
    parser.add_argument('--config', dest='config_paths', action='append', default=None,
                        help='Path to a JSON config file. Repeat for layered configs '
                             '(later files override earlier). Also reads $BOT_CONFIG '
                             'if no --config flag is supplied. Layering precedence: '
                             'CLI > env > JSON > dataclass defaults.')

    # Simple MA strategy parameters
    parser.add_argument('--fast-ma-period', type=int, help='Fast MA period (simple_ma)')
    parser.add_argument('--slow-ma-period', type=int, help='Slow MA period (simple_ma)')

    # RSI strategy parameters
    parser.add_argument('--rsi-period', type=int, help='RSI period (rsi)')
    parser.add_argument('--oversold-threshold', type=int, help='RSI oversold threshold (rsi)')
    parser.add_argument('--overbought-threshold', type=int, help='RSI overbought threshold (rsi)')
    parser.add_argument('--rsi-extreme-low', type=int, help='RSI extreme low for size increase (rsi, default: 25)')
    parser.add_argument('--rsi-moderate-low', type=int, help='RSI moderate low for size increase (rsi, default: 35)')
    parser.add_argument('--size-multiplier-extreme', type=float,
                        help='Size multiplier when RSI < extreme_low (rsi, default: 1.5)')
    parser.add_argument('--size-multiplier-moderate', type=float,
                        help='Size multiplier when RSI < moderate_low (rsi, default: 1.2)')

    # Bollinger Bands parameters
    parser.add_argument('--bb-period', type=int, help='Bollinger Bands period (bollinger_bands)')
    parser.add_argument('--std-dev', type=float, help='Standard deviation (bollinger_bands)')
    parser.add_argument('--squeeze-threshold', type=float, help='Squeeze threshold (bollinger_bands)')
    parser.add_argument('--volatility-expansion-threshold', type=float,
                        help='Volatility expansion multiplier (bollinger_bands, default: 1.5)')
    parser.add_argument('--high-band-width-threshold', type=float,
                        help='Band width threshold to reduce size (bollinger_bands, default: 0.05)')
    parser.add_argument('--high-band-width-multiplier', type=float,
                        help='Size multiplier when band width is high (bollinger_bands, default: 0.8)')
    parser.add_argument('--low-band-width-threshold', type=float,
                        help='Band width threshold to increase size (bollinger_bands, default: 0.02)')
    parser.add_argument('--low-band-width-multiplier', type=float,
                        help='Size multiplier when band width is low (bollinger_bands, default: 1.2)')

    # MACD parameters
    parser.add_argument('--fast-ema', type=int, help='Fast EMA period (macd)')
    parser.add_argument('--slow-ema', type=int, help='Slow EMA period (macd)')
    parser.add_argument('--signal-ema', type=int, help='Signal EMA period (macd)')
    parser.add_argument('--divergence-lookback', type=int, help='Divergence detection lookback (macd, default: 20)')
    parser.add_argument('--histogram-strength-high', type=float,
                        help='Histogram strength to increase size (macd, default: 0.5)')
    parser.add_argument('--histogram-strength-low', type=float,
                        help='Histogram strength to reduce size (macd, default: 0.1)')
    parser.add_argument('--histogram-multiplier-high', type=float,
                        help='Size multiplier for strong histogram (macd, default: 1.3)')
    parser.add_argument('--histogram-multiplier-low', type=float,
                        help='Size multiplier for weak histogram (macd, default: 0.7)')

    # Grid Trading parameters
    parser.add_argument('--grid-levels', type=int, help='Number of grid levels (grid_trading)')
    parser.add_argument('--grid-spacing-pct', type=float, help='Grid spacing percentage (grid_trading)')
    parser.add_argument('--position-size-per-grid', type=float, help='Position size per grid (grid_trading)')
    parser.add_argument('--range-period', type=int, help='Range period (grid_trading)')
    parser.add_argument('--range-pct-threshold', type=float,
                        help='Max range %% for ranging market (grid_trading, default: 10)')
    parser.add_argument('--volatility-threshold', type=float,
                        help='Max volatility for ranging market (grid_trading, default: 0.15)')
    parser.add_argument('--grid-recalc-bars', type=int,
                        help='Bars between grid recalculation (grid_trading, default: 20)')
    parser.add_argument('--grid-saturation-threshold', type=float,
                        help='Grid fill ratio to reduce size (grid_trading, default: 0.7)')
    parser.add_argument('--grid-boundary-margin-low', type=float,
                        help='Low boundary margin (grid_trading, default: 0.98)')
    parser.add_argument('--grid-boundary-margin-high', type=float,
                        help='High boundary margin (grid_trading, default: 1.02)')

    # Breakout parameters
    parser.add_argument('--lookback-period', type=int, help='Lookback period (breakout)')
    parser.add_argument('--volume-multiplier', type=float, help='Volume multiplier (breakout)')
    parser.add_argument('--breakout-confirmation-bars', type=int, help='Breakout confirmation bars (breakout)')
    parser.add_argument('--atr-period', type=int, help='ATR period (breakout)')
    parser.add_argument('--pivot-window', type=int, help='Pivot detection window (breakout, default: 5)')
    parser.add_argument('--avg-volume-lookback', type=int, help='Average volume lookback bars (breakout, default: 20)')
    parser.add_argument('--stop-loss-atr-multiplier', type=float,
                        help='Stop loss ATR multiplier (breakout, default: 1.5)')
    parser.add_argument('--position-stop-loss-atr-multiplier', type=float,
                        help='Position stop loss ATR multiplier (breakout, default: 2.0)')
    parser.add_argument('--strong-breakout-multiplier', type=float,
                        help='Size multiplier for strong breakout (breakout, default: 1.5)')
    parser.add_argument('--high-atr-threshold', type=float, help='ATR %% to reduce size (breakout, default: 3.0)')
    parser.add_argument('--low-atr-threshold', type=float, help='ATR %% to increase size (breakout, default: 1.0)')
    parser.add_argument('--high-atr-multiplier', type=float,
                        help='Size multiplier for high ATR (breakout, default: 0.7)')
    parser.add_argument('--low-atr-multiplier', type=float, help='Size multiplier for low ATR (breakout, default: 1.3)')

    # Market Making parameters
    parser.add_argument('--spread-bps', type=float,
                        help='Spread from mid price in basis points (market_making, default: 5)')
    parser.add_argument('--order-size-usd', type=float, help='Size per order in USD (market_making, default: 50)')
    parser.add_argument('--max-open-orders', type=int, help='Max concurrent open orders (market_making, default: 4)')
    parser.add_argument('--refresh-interval', dest='refresh_interval_seconds', type=float,
                        help='Seconds before cancelling stale orders (market_making, default: 30)')
    parser.add_argument('--refresh-tolerance-bp', type=float,
                        help='Keep an order across cycles when its price drifted no more than this many '
                             'basis points from the current ideal price (market_making, default: 0 = disabled). '
                             'Preserves queue priority and reduces API churn when the market is quiet.')
    parser.add_argument('--refresh-max-age-seconds', type=float,
                        help='Safety-net upper bound on the age of a kept order even when its price is still '
                             'within tolerance (market_making, default: refresh_interval_seconds * 4).')
    parser.add_argument('--no-close-immediately', action='store_true', default=False,
                        help='Disable immediate position closing (market_making)')
    parser.add_argument('--drain-flag-file', type=str,
                        help='Path to a flag file. When the file exists, the strategy enters '
                             'drain mode: stops placing new entry orders and only manages '
                             'existing positions until the file is removed. Used to gracefully '
                             'unwind before a session switch. Empty/unset = disabled. (market_making)')
    parser.add_argument('--max-position-age', dest='max_position_age_seconds', type=float,
                        help='Max seconds to hold a position before force-closing (market_making, default: 120)')
    parser.add_argument('--maker-only', action='store_true', default=False,
                        help='Use post-only (maker) orders for all trades including closes (market_making)')
    parser.add_argument('--taker-fallback-age', dest='taker_fallback_age_seconds', type=float,
                        help='Seconds after max-position-age to fall back to taker for force-close. '
                             'Not set = never use taker. 0 = taker at max-position-age. (market_making)')
    parser.add_argument('--aggressive-loss-bps', type=float,
                        help='Max loss in bps accepted to avoid taker close (default: 1.0). '
                             '0 = breakeven only, no loss-accepting tier. (market_making)')
    parser.add_argument('--force-close-max-loss-bps', type=float,
                        help='Max loss bps during force-close phase, scaling from aggressive-loss-bps '
                             '(default: 0 = disabled, use BBO-only pricing)')
    parser.add_argument('--close-spread-bps', type=float,
                        help='Close order spread in bps (default: same as --spread-bps). '
                             'Lower values increase maker close fill rate.')
    parser.add_argument('--close-breakeven-pct', type=float,
                        help='Fraction of max_position_age at which close tier transitions to breakeven '
                             '(default: 0.50, i.e. 50%% of max age)')
    parser.add_argument('--close-aggressive-pct', type=float,
                        help='Fraction of max_position_age at which close tier transitions to aggressive '
                             '(default: 0.75, i.e. 75%% of max age)')
    parser.add_argument('--close-tier-min-seconds', type=float,
                        help='Absolute floor in seconds for the breakeven tier transition. '
                             'Guards against over-shortening when dynamic age, per-coin overrides '
                             'and toxicity acceleration stack (default: 0 = disabled)')
    parser.add_argument('--coin-close-tier-overrides', type=str,
                        help='Per-coin close tier overrides "COIN:BREAKEVEN/AGGRESSIVE,..." '
                             '(e.g. "NVDA:0.30/0.55,xyz:XYZ100:0.40/0.65"). Requires '
                             '0 <= breakeven < aggressive <= 1. Falls back to '
                             '--close-breakeven-pct / --close-aggressive-pct.')
    parser.add_argument('--close-tier-toxicity', dest='close_tier_toxicity_enabled',
                        action='store_true', default=None,
                        help='Accelerate close tier transitions when recent markout indicates '
                             'toxic flow. Requires --enable-adverse-selection-log and WebSocket; '
                             'falls back to base behaviour when unavailable. (market_making)')
    parser.add_argument('--close-tier-toxicity-threshold-bps', type=float,
                        help='Window-average markout at or below which acceleration fires '
                             '(must be <= 0; negative = adverse; default: -2.0)')
    parser.add_argument('--close-tier-toxicity-window', type=str,
                        choices=['5s', '30s', '60s'],
                        help='Adverse-selection sample window to read (default: 30s)')
    parser.add_argument('--close-tier-toxicity-multiplier', type=float,
                        help='Multiplier applied to tier percentages while firing '
                             '(0.1-1.0, default: 0.6)')
    parser.add_argument('--close-tier-toxicity-min-fills', type=int,
                        help='Minimum fills in the window required to fire (default: 5)')
    parser.add_argument('--close-tier-toxicity-floor-pct', type=float,
                        help='Lower bound on breakeven_pct after the multiplier (default: 0.15)')
    parser.add_argument('--bbo-mode', action='store_true',
                        help='Place orders at BBO instead of mid±spread (market_making)')
    parser.add_argument('--bbo-offset-bps', type=float,
                        help='Offset from BBO in bps (0=at BBO, 1=1bp behind). '
                             'Defaults to 0.1 when --maker-only to reduce Alo rejection risk. '
                             'Negative values are clamped to 0. (market_making)')
    parser.add_argument('--inventory-skew-bps', type=float,
                        help='Skew prices per unit of inventory to encourage position reduction. '
                             '0=disabled, 2=shift 2bps per order-size of inventory. (market_making)')
    parser.add_argument('--imbalance-threshold', type=float,
                        help='Skip quoting one side when L2 book imbalance exceeds this (0-1). '
                             '0=disabled, 0.4=skip when 70%%/30%% imbalance. (market_making)')
    parser.add_argument('--loss-streak-limit', type=int,
                        help='Cooldown a coin after this many consecutive losses. '
                             '0=disabled, 2=cooldown after 2 losses. (market_making)')
    parser.add_argument('--loss-streak-cooldown', type=float,
                        help='Seconds to pause a coin after hitting loss streak limit (default: 300, market_making)')
    parser.add_argument('--bbo-guard-threshold-bps', type=float,
                        help='Cancel orders when BBO changes by this many bps; 0 to disable (default: 2.0)')
    parser.add_argument('--imbalance-guard-threshold', type=float,
                        help='Cancel one side when L2 imbalance exceeds this (0-1); 0 to disable (default: 0)')
    parser.add_argument('--imbalance-guard-depth', type=int,
                        help='Number of L2 book levels for imbalance guard calculation (default: 5)')
    parser.add_argument('--enable-adverse-selection-log', action='store_true', default=False,
                        help='Enable post-fill adverse selection measurement logging')
    parser.add_argument('--adverse-selection-log-interval', type=float,
                        help='Adverse selection summary log interval in seconds (default: 300)')
    parser.add_argument('--fill-feature-log', dest='fill_feature_log_enabled',
                        action='store_true', default=None,
                        help='Write per-fill order book features + markout labels to a daily '
                             'JSONL file for ML dataset building (requires --enable-ws and '
                             '--enable-adverse-selection-log)')
    parser.add_argument('--fill-feature-log-dir', type=str,
                        help='Directory for fill feature JSONL files '
                             '(default: tracking/fill_features)')
    parser.add_argument('--fill-feature-flush-interval', type=float,
                        help='Fill feature flush interval in seconds (default: 60)')
    parser.add_argument('--fill-feature-max-daily-bytes', type=int,
                        help='Max bytes per daily fill feature file; excess records are '
                             'dropped (default: 50000000)')
    parser.add_argument('--fill-feature-buffer-max', type=int,
                        help='Max in-memory buffered fill feature records (default: 5000)')
    parser.add_argument('--rejection-log-level', type=str,
                        choices=['error', 'warning', 'info', 'debug'],
                        help='Log level for routine post-only order rejections '
                             '(default: error — preserves legacy behaviour). '
                             'Set to "warning" to reduce ERROR noise once the '
                             '5min summary line is trusted.')
    parser.add_argument('--rejection-summary-interval', type=float,
                        help='Order rejection aggregate summary interval in seconds; '
                             '0 disables the summary (default: 300)')
    parser.add_argument('--coin-offset-overrides', type=str, default='',
                        help='Per-coin BBO offset overrides in bps (e.g. "SP500:0.5,MSFT:3")')
    parser.add_argument('--coin-spread-overrides', type=str, default='',
                        help='Per-coin spread overrides in bps (e.g. "SP500:8,XYZ100:15")')
    parser.add_argument('--coin-size-overrides', type=str, default='',
                        help='Per-coin order size overrides in USD (e.g. "TSLA:150,NVDA:150")')
    parser.add_argument('--coin-unrealized-loss-overrides', type=str, default='',
                        help='Per-coin unrealized-loss early-close threshold in bps '
                             '(e.g. "INTC:25,OIL:10"). Falls back to --unrealized-loss-close-bps. '
                             'Setting an override to 0 disables the feature for that coin.')
    parser.add_argument('--close-refresh-threshold-bps', type=float,
                        help='Refresh close orders when BBO changes by this many bps; 0 to disable (default: 0)')
    parser.add_argument('--spread-schedule', type=str, default='',
                        help='Per-hour spread multiplier schedule: "HOUR:MULT,..." (e.g. "14:1.5,15:1.5,3:2.0")')
    parser.add_argument('--quiet-hours-utc', type=str, default='',
                        help='UTC hours to reduce/stop quoting, comma-separated (e.g. "17" or "17,18")')
    parser.add_argument('--quiet-hours-spread-multiplier', type=float, default=0.0,
                        help='Spread multiplier during quiet hours (0=stop quoting, >0=widen spread)')
    parser.add_argument('--coin-market-overrides', type=str, default='',
                        help='Per-coin market calendar: "COIN:MARKET,..." '
                             '(e.g. "NVDA:NYSE,SP500:CME_INDEX"); ids: '
                             'NYSE/CME_INDEX/KRX/TSE/HKEX/FX/NONE (market_making)')
    parser.add_argument('--phase-spread-multipliers', type=str, default='',
                        help='Per-phase spread multiplier for market-assigned '
                             'coins: "PHASE:MULT,..." (e.g. '
                             '"open_window:0,close_window:3.0,closed:2.5,'
                             'regular:2.0,lunch:0"; 0=stop quoting) (market_making)')
    parser.add_argument('--phase-open-window-minutes', type=float, default=None,
                        help='Minutes after any market open counted as the '
                             'open_window phase (default: 30) (market_making)')
    parser.add_argument('--phase-close-window-minutes', type=float, default=None,
                        help='Minutes before any market close counted as the '
                             'close_window phase (default: 30) (market_making)')
    parser.add_argument('--microprice-skew', dest='microprice_skew_enabled',
                        action='store_true', default=False,
                        help='Enable micro-price asymmetric offset for taker fill prevention (market_making)')
    parser.add_argument('--microprice-skew-multiplier', type=float,
                        help='Micro-price skew scaling factor (default: 1.0, market_making)')
    parser.add_argument('--microprice-max-skew-bps', type=float,
                        help='Max offset adjustment from micro-price skew in bps (default: 2.0)')
    parser.add_argument('--velocity-guard', dest='velocity_guard_enabled',
                        action='store_true', default=False,
                        help='Enable BBO velocity guard — cancel orders on sustained directional BBO moves')
    parser.add_argument('--velocity-consecutive', type=int,
                        help='Number of consecutive same-direction BBO moves to trigger cancel (default: 3)')
    parser.add_argument('--velocity-min-move-bps', type=float,
                        help='Minimum cumulative BBO move in bps to trigger cancel (default: 1.0)')
    parser.add_argument('--oracle-guard', dest='oracle_guard_enabled',
                        action='store_true', default=None,
                        help='Enable oracle divergence/momentum guard — cancel stale-side quotes '
                             'when book mid diverges from the HIP-3 oraclePx stream or oracle '
                             'updates show sustained momentum (requires --enable-ws)')
    parser.add_argument('--oracle-divergence-threshold-bps', type=float,
                        help='|mid - oraclePx| divergence in bps that cancels the stale side '
                             '(default: 5.0; 0 disables the divergence gate)')
    parser.add_argument('--oracle-momentum-cap-pin-bps', type=float,
                        help='Single oracle step in bps treated as 1%%-cap pinning — fires the '
                             'momentum gate immediately (default: 80.0; 0 disables pin detection)')
    parser.add_argument('--oracle-momentum-min-step-bps', type=float,
                        help='Minimum oracle step in bps counted toward consecutive momentum '
                             '(default: 5.0)')
    parser.add_argument('--oracle-momentum-consecutive', type=int,
                        help='Consecutive same-direction oracle steps that fire the momentum gate '
                             '(default: 3; 0 disables consecutive detection)')
    parser.add_argument('--oracle-stale-ttl-seconds', type=float,
                        help='Disarm the guard for a coin when its oraclePx value has not changed '
                             'for this many seconds — automatic market-close detection (default: 30)')
    parser.add_argument('--oracle-guard-block-seconds', type=float,
                        help='Placement block duration after a guard fire (default: 10)')
    parser.add_argument('--dynamic-offset', dest='dynamic_offset_enabled',
                        action='store_true', default=False,
                        help='Enable dynamic offset auto-adjustment based on adverse selection (requires --enable-ws)')
    parser.add_argument('--dynamic-offset-sensitivity', type=float,
                        help='Offset widening per 1bps of adverse selection (default: 0.5)')
    parser.add_argument('--dynamic-offset-tighten-rate', type=float,
                        help='Offset tightening rate for favorable fills (default: 0.25)')
    parser.add_argument('--dynamic-offset-max-add', dest='dynamic_offset_max_addition', type=float,
                        help='Max offset addition in bps (default: 3.0)')
    parser.add_argument('--dynamic-offset-max-reduce', dest='dynamic_offset_max_reduction', type=float,
                        help='Max offset reduction in bps (default: 1.0)')
    parser.add_argument('--dynamic-offset-floor', type=float,
                        help='Minimum offset floor in bps (default: 0.5)')
    parser.add_argument('--dynamic-offset-min-fills', type=int,
                        help='Min fills before dynamic adjustment activates (default: 5)')
    parser.add_argument('--unrealized-loss-close-bps', type=float,
                        help='Close position early via taker when unrealized loss exceeds this bps threshold; '
                             '0 = disabled (default: 0, market_making)')
    parser.add_argument('--vol-adjust', dest='vol_adjust_enabled',
                        action='store_true', default=False,
                        help='Enable volatility-adjusted BBO offset (market_making)')
    parser.add_argument('--vol-adjust-multiplier', type=float,
                        help='Volatility multiplier for offset adjustment (default: 2.0, market_making)')
    parser.add_argument('--vol-lookback', type=int,
                        help='Number of recent mid prices for volatility calc (default: 30, market_making)')
    parser.add_argument('--vol-adjust-max-offset', type=float,
                        help='Max BBO offset in bps after vol adjustment (default: 50, market_making)')
    parser.add_argument('--dynamic-age', dest='dynamic_age_enabled',
                        action='store_true', default=False,
                        help='Enable volatility-adjusted MAX_POSITION_AGE (market_making)')
    parser.add_argument('--dynamic-age-baseline-vol', type=float,
                        help='Baseline volatility in bps for dynamic age scaling (default: 1.0, market_making)')
    parser.add_argument('--dynamic-age-min', type=float,
                        help='Minimum position age in seconds when volatility is high (default: 60, market_making)')
    parser.add_argument('--dynamic-age-max', type=float,
                        help='Maximum position age in seconds when volatility is low (default: 300, market_making)')
    parser.add_argument('--auto-exclude', dest='auto_exclude_enabled',
                        action='store_true', default=False,
                        help='Auto-pause a coin when adverse selection stays past --auto-exclude-threshold-bps '
                             'for --auto-exclude-consecutive consecutive summary windows (requires '
                             '--enable-adverse-selection-log)')
    parser.add_argument('--auto-exclude-threshold-bps', type=float,
                        help='Adverse-selection threshold (bps) for auto-exclude; values <= this trigger '
                             '(default: -3.0, market_making)')
    parser.add_argument('--auto-exclude-consecutive', type=int,
                        help='Consecutive adverse summary windows required to trigger auto-exclude '
                             '(default: 3, market_making)')
    parser.add_argument('--auto-exclude-min-fills', type=int,
                        help='Minimum fills per summary window for auto-exclude to consider it '
                             '(default: 3, market_making)')
    parser.add_argument('--auto-exclude-cooldown', type=int,
                        help='Cooldown seconds after auto-exclude triggers (default: 1800, market_making)')
    parser.add_argument('--auto-exclude-window-label', type=str,
                        help='Adverse-selection sample window for auto-exclude: 5s|30s|60s '
                             '(default: 60s, market_making)')

    # Forager: composite-score auto-exclude (orthogonal to auto_exclude)
    parser.add_argument('--forager', dest='forager_enabled',
                        action='store_true', default=False,
                        help='Auto-exclude a coin when its composite health score (activity + close '
                             'maker rate + cost) stays below --forager-threshold for '
                             '--forager-consecutive checks in a row (market_making)')
    parser.add_argument('--forager-threshold', dest='forager_score_threshold', type=float,
                        help='Composite health score threshold (0-100); below this triggers '
                             '(default: 30.0, market_making)')
    parser.add_argument('--forager-consecutive', type=int,
                        help='Consecutive sub-threshold checks required to trigger forager exclude '
                             '(default: 3, market_making)')
    parser.add_argument('--forager-cooldown', dest='forager_cooldown_seconds', type=int,
                        help='Cooldown seconds after forager triggers (default: 1800, market_making)')
    parser.add_argument('--forager-w-activity', dest='forager_weight_activity', type=float,
                        help='Composite-score weight for activity dimension (default: 0.3, market_making)')
    parser.add_argument('--forager-w-quality', dest='forager_weight_quality', type=float,
                        help='Composite-score weight for close-quality dimension (default: 0.4, market_making)')
    parser.add_argument('--forager-w-cost', dest='forager_weight_cost', type=float,
                        help='Composite-score weight for cost dimension (default: 0.3, market_making)')

    # Risk guardrail parameters
    parser.add_argument('--max-position-pct', type=float,
                        help='Max single position as %% of account (default: 0.2)')
    parser.add_argument('--max-margin-usage', type=float,
                        help='Stop new orders above this margin usage (default: 0.8)')
    parser.add_argument('--force-close-margin', type=float,
                        help='Force close ALL positions above this margin ratio (disabled by default)')
    parser.add_argument('--force-close-leverage', type=float,
                        help='Force close ALL positions above this leverage (disabled by default)')
    parser.add_argument('--daily-loss-limit', type=float,
                        help='Absolute $ daily loss to auto-stop the bot (disabled by default)')
    parser.add_argument('--per-trade-stop-loss', type=float,
                        help='Cut losing trades at this %% loss (e.g. 0.05 = 5%%, disabled by default)')
    parser.add_argument('--max-open-positions', type=int,
                        help='Max concurrent open positions (default: 5)')
    parser.add_argument('--cooldown-after-stop', type=int,
                        help='Seconds to wait after emergency stop (default: 3600)')
    parser.add_argument('--risk-level', type=str,
                        choices=['green', 'yellow', 'red', 'black'],
                        help='Dynamic risk level: green=100%%, yellow=50%%, red=pause, black=close all')
    return parser
