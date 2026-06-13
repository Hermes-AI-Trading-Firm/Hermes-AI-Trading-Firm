# Complete Strategy Specification

- **Spec ID**: MGC_VWAP_PULLBACK_v001
- **Status**: specification-complete
- **Created By**: Quant Research

## Strategy

- **Strategy Name**: MGC VWAP Pullback to Session Trend
- **Market**: Futures
- **Symbol**: MGC
- **Timeframe**: 3-minute (3m)
- **Session**: COMEX Gold electronic session
- **Strategy Type**: Mean Reversion / VWAP / Pullback

## Overview

Intraday mean-reversion strategy on Micro Gold futures that buys pullbacks to VWAP when the session trend is up, and sells rallies when the session trend is down.

## Entry Rules

1. Calculate session VWAP and VWAP +/- 1 standard deviation bands.
2. Determine session trend from first 30 minutes:
   - Bullish bias if price remains above VWAP.
   - Bearish bias if price remains below VWAP.
3. Long entry:
   - Price pulls back to touch or cross below lower VWAP band.
   - On the same 3-minute bar, price closes back above VWAP.
   - Enter on the next 3-minute bar open.
4. Short entry:
   - Price pulls back to touch or cross above upper VWAP band.
   - On the same 3-minute bar, price closes back below VWAP.
   - Enter on the next 3-minute bar open.

## Exit Rules

- Long exit:
  - Close below lower VWAP band after entry, OR close below session VWAP.
- Short exit:
  - Close above upper VWAP band after entry, OR close above session VWAP.
- Time exit:
  - Flat 5 minutes before session end to avoid overnight risk.
- Circuit-breaker exit:
  - If COMEX circuit-breaker thresholds are breached, flatten immediately.

## Stop Loss Rules

- Long initial stop:
  - Below the pullback swing low by 1 tick; minimum 0.50 points.
- Short initial stop:
  - Above the pullback swing high by 1 tick; minimum 0.50 points.
- Position size reduced or trade skipped if stop distance exceeds 1.5% of MGC notional value.
- Optional volatility-adjusted stop:
  - If 3-minute ATR is > 1.5x average, move stop to 1.5 x ATR-based distance.

## Profit Target Rules

- Partial scale-out:
  - 50% at first target; 50% at runner.
- Long first target:
  - VWAP + 0.5 sigma.
- Short first target:
  - VWAP - 0.5 sigma.
- Long runner:
  - VWAP + 1.5 sigma.
- Short runner:
  - VWAP - 1.5 sigma.
- Runner management:
  - If runner not reached within 6 bars of first target, move runner stop to breakeven.

## Risk Rules

- Max risk per trade: 0.75% of account equity.
- No overlapping trades; only one position at a time.
- Do not trade within the first 5 minutes after session open; require VWAP stabilization.
- Skip trade if VWAP band width is less than 5 ticks for 3 consecutive bars.
- Max intraday drawdown limit: 15% on MGC notional value.

## Filters

- Volatility filter:
  - Band width must exceed 5 ticks.
- Session filter:
  - Trade only active COMEX session; avoid low-volume overlaps.
- News filter:
  - Avoid NFP and Fed announcements in the gold session.

## Regime Assumptions

- Best in low-to-moderate volatility mean-reverting environments.
- Degrades during strong directional news-driven moves away from VWAP.
- Must be tested in:
  - Trending bull
  - Trending bear
  - Sideways/range
  - Transition/volatility expansion
- Key regime indicator:
  - Session VWAP slope and band width expansion/contraction.

## Backtest Requirements

- Data source: COMEX-quality 3-minute MGC session data.
- Data length: minimum 2 years.
- Commission/slippage:
  - Realistic gold futures commission and slippage.
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
  - Equity curve
  - Trade list with regime tags

## Suggested Optimization Variables

- Trend detection window: 20–40 minutes in 5-minute steps.
- VWAP band multiplier: 0.75–1.5 sigma in 0.25 steps.
- Timeframe: 2m / 3m / 5m in 1m steps.
- First target offset: 0.25–1.0 sigma in 0.25 steps.
- Runner target offset: 1.0–2.5 sigma in 0.25 steps.
- Max hold bars: 20–60 bars in 5-bar steps.
- Band width filter: 5–15 ticks in 2.5 steps.

## Approval Criteria

- Profit factor: >= 1.20.
- Expectancy: positive.
- Max drawdown: <= 25%.
- Total trades: >= 30.
- No severe walk-forward OOS degradation (>30% drop).
- Monte Carlo survival rate >= 85%.
- Stable parameter zones across optimization sweeps.
- Regime-specific performance documented.

## Failure Conditions

- Strong directional news moves push price away from VWAP without retracing.
- Low-volume Asian overlap sessions.
- Band compression then explosive volatility.
- Win rate drops below 55% over 20-trade window.
- Max drawdown exceeds 15% intraday.
