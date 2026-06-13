# Strategy Specification

- **Spec ID**: BTC_REGIME_BREAKOUT_v001
- **Status**: draft
- **Created By**: Strategy Factory → Quant Research

## Overview

Regime-filtered breakout strategy on Bitcoin/USDT. Trades breakouts only in high-volatility trend regimes; sits out sideways/transition regimes.

## Market

- Asset Class: crypto
- Symbol: BTCUSDT
- Exchange: Binance (spot or perpetual futures)
- Session: 24/7 (use UTC for consistency)
- Tick Size: 0.01
- Pip Value: $0.01 per tick (1.00 = $1 per BTC)

## Timeframe

- Chart: 1-hour (1h)
- Higher-timeframe context: daily chart for regime classification

## Entry Rules

1. Determine market regime using a simple rule-based model on daily timeframe:
   - Bull: 20-period SMA > 50-period SMA AND ATR(14) > median ATR(14) of last 90 days.
   - Bear: 20-period SMA < 50-period SMA AND ATR(14) > median ATR(14) of last 90 days.
   - Sideways/Transition: neither bull nor bear.
2. Trade only in Bull or Bear regimes. Do not trade in Sideways or Transition.
3. Define 4-hour consolidation box (highest high / lowest low over last 8 bars on 1-hour chart).
4. Long Entry:
   - Bull regime confirmed.
   - 1-hour close breaks above the 4-hour consolidation high.
   - Enter on next 1-hour bar open.
5. Short Entry:
   - Bear regime confirmed.
   - 1-hour close breaks below the 4-hour consolidation low.
   - Enter on next 1-hour bar open.

## Exit Rules

- **Regime Flip Exit**: If daily regime flips from Bull to Sideways or Bear, exit all longs immediately; exit all shorts on Bear→Sideways/Bull.
- **Trend Exhaustion Exit**: Long exit if close returns below consolidation midpoint after entry; short exit if close returns above midpoint.
- Time exit: flat after 3 days if neither target nor stop hit (carry cost consideration on futures).

## Stop Rules

- **Long Stop**: Below consolidation low minus 1× 1-hour ATR(14).
- **Short Stop**: Above consolidation high plus 1× 1-hour ATR(14).
- Stop must be placed beyond recent swing points to avoid noise.

## Target Rules

- **Long Target**: 1.5× risk (R-multiple) measured from entry to initial stop.
- **Short Target**: 1.5× risk (R-multiple) measured from entry to initial stop.
- Optional partial exit: 50% at 1R, remainder trailing 1× ATR(14) from highest high since entry (long) or lowest low (short).

## Risk Rules

- Max risk per trade: 2% of account equity.
- Max open positions: 1.
- Max exposure: 4% of equity in this strategy at any time.
- Do not enter if 4-hour box width is < 0.5% of current BTC price (insufficient volatility).
- Do not enter if ATR(14) daily is below 25th percentile of last 6 months (regime is "low volatility" even if trend follows).
- Size based on notional value and stop distance in USDT.

## Edge Hypothesis

- Crypto breakouts are more reliable when a higher-timeframe trend is present; regime filtering removes chop.
- High ATR in strong regimes indicates conviction; breakout failures are less frequent.
- Consolidation boxes represent compressed energy; breakouts often lead to impulsive moves in trending crypto markets.

## Failure Conditions

- Regime model lags regime transitions; whipsaws during regime shifts.
- Flash crashes or pump-and-dumps cause extreme slippage beyond stop levels.
- Box breakout fails if volume on breakout bar is not above 20-period average 1-hour volume.
- Performance degrades in tight 4-hour boxes; breakout rate collapses.
- Strategy underperforms simple buy-and-hold in extended bull runs without regime-aware exits.

## Suggested Optimization Variables

| Parameter | Range | Step |
|-----------|-------|------|
| SMA fast (days) | 10–30 | 5 |
| SMA slow (days) | 40–70 | 5 |
| ATR regime threshold (percentile) | 25%–75% | 10% |
| Consolidation box length (1h bars) | 4–16 | 2 |
| Risk multiplier (target) | 1.2–2.0 | 0.2 |
| Time exit (bars) | 24–168 | 12 |
| Volume filter (× 20-bar avg) | 1.0–2.0 | 0.25 |

## Notes

- Data must include full 24h crypto data; daily gaps matter.
- Backtest should note exchange-specific fills; Binance spot vs futures fees/slippage differ.
- Regime analysis should also test HMM inferred regimes and compare to rule-based.
- Forward-test in both spot and futures to confirm robustness.
