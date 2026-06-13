# Complete Strategy Specification

- **Spec ID**: BTC_REGIME_BREAKOUT_v001
- **Status**: specification-complete
- **Created By**: Quant Research

## Strategy

- **Strategy Name**: BTC Regime-Filtered Consolidation Breakout
- **Market**: Crypto
- **Symbol**: BTCUSDT
- **Timeframe**: 1-hour (1h)
- **Session**: 24-hour crypto market; use UTC session timestamps.
- **Strategy Type**: Regime Filtered Breakout / Trend Following

## Overview

Plays breakouts from multi-hour consolidations only when higher-timeframe regime supports strong directional conviction. Sits out sideways and transition regimes.

## Entry Rules

1. Daily regime classification:
   - Bull regime: 20 SMA > 50 SMA AND 14-day ATR above 90-day median ATR.
   - Bear regime: 20 SMA < 50 SMA AND 14-day ATR above 90-day median ATR.
   - Sideways or transition otherwise.
2. Trade only in Bull or Bear regimes; do not trade Sideways or Transition.
3. Build 4-hour consolidation box from lowest 8 bars on 1-hour chart.
4. Long entry:
   - Bull regime confirmed.
   - 1-hour close breaks above 4-hour box high.
   - Enter on next 1-hour bar open.
5. Short entry:
   - Bear regime confirmed.
   - 1-hour close breaks below 4-hour box low.
   - Enter on next 1-hour bar open.

## Exit Rules

- Regime flip exit:
  - Close all longs if daily regime flips from Bull to Sideways or Bear.
  - Close all shorts if daily regime flips from Bear to Sideways or Bull.
- Trend exhaustion exit:
  - Long exit if close returns below consolidation midpoint.
  - Short exit if close returns above consolidation midpoint.
- Time exit:
  - Flat after 3 days if neither target nor stop hit.

## Stop Loss Rules

- Long initial stop:
  - Below consolidation low minus 1 x 1-hour ATR(14).
- Short initial stop:
  - Above consolidation high plus 1 x 1-hour ATR(14).
- Stop not placed inside obvious recent swing structure.

## Profit Target Rules

- Base target:
  - 1.5 x initial risk (R-multiple).
- Partial exit:
  - Optional 50% at 1R; remainder trails 1 x ATR(14) from the favorable extreme since entry.
- Trailing stop activation:
  - When runner reaches 1.5R, move stop to breakeven.

## Risk Rules

- Maximum risk per trade: 2% of account equity.
- Maximum one open position.
- Maximum total exposure: 4% of equity.
- Do not enter if 4-hour box width is less than 0.5% of current BTC price.
- Do not enter if daily ATR is below 25th percentile of 6-month history.
- Size based on notional value and stop distance in USDT.

## Filters

- Regime filter:
  - Only trade when daily regime is Bull or Bear.
- Volatility filter:
  - Daily ATR >= 25th percentile of 6-month history.
- Consolidation quality filter:
  - Box width >= 0.5% of current price.
- Volume filter:
  - Breakout bar volume above 20-period average 1-hour volume.

## Regime Assumptions

- Strategy is regime-contingent.
- Expected best regimes:
  - Strong trending bull and strong trending bear.
- Expected worst regimes:
  - Sideways and transition.
- Required analyses:
  - Markov transition matrix
  - Hidden Markov Model
  - Regime-filtered vs unfiltered comparison tables
- Do not approve without explicit regime performance report.

## Backtest Requirements

- Data source: Binance-quality 1-hour BTCUSDT data.
- Data length: minimum 2 years.
- Fees/slippage:
  - Use realistic Binance fees for both spot and futures.
- Minimum acceptable trade count: 30 trades.
- Report:
  - Net profit
  - Profit factor
  - Win rate
  - Max drawdown
  - Average win / average loss
  - Expectancy per trade
  - Sharpe ratio
  - Recovery factor
  - Equity curve by regime
  - Trade list with regime tags

## Suggested Optimization Variables

- Fast SMA: 10–30 days in 5-day steps.
- Slow SMA: 40–70 days in 5-day steps.
- ATR regime threshold: 25–75 percentile in 10-percentile steps.
- Consolidation box length: 4–16 bars in 2-bar steps.
- Risk multiplier: 1.2–2.0 in 0.2 steps.
- Time exit bars: 24–168 in 12-bar steps.
- Volume multiplier: 1.0–2.0 x 20-bar average in 0.25 steps.

## Approval Criteria

- Profit factor: >= 1.20.
- Expectancy: positive.
- Max drawdown: <= 25%.
- Total trades: >= 30.
- Walk-forward OOS degradation not greater than 30%.
- Monte Carlo survival rate >= 85%.
- Stable parameter zones across optimization sweeps.
- Documented best and worst regimes.

## Failure Conditions

- Regime model lags transitions.
- Flash crashes and pump-and-dumps cause extreme slippage.
- Breakout bar lacks volume confirmation.
- Tight 4-hour boxes cause breakout rate collapse.
- Strong bull runs outperform strategy due to early upside cap from short exits or regime flips.
