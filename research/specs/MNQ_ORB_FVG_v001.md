# Complete Strategy Specification

- **Spec ID**: MNQ_ORB_FVG_v001
- **Status**: specification-complete
- **Created By**: Quant Research

## Strategy

- **Strategy Name**: MNQ Opening Range Breakout + Fair Value Gap Continuation
- **Market**: Futures
- **Symbol**: MNQ
- **Timeframe**: 5-minute (5m)
- **Session**: US Regular (09:30–16:00 ET)
- **Strategy Type**: Breakout / Liquidity / Opening Range

## Overview

Intraday breakout strategy on Micro E-mini Nasdaq-100 that combines an opening range breakout with a Fair Value Gap (FVG) confirmation filter to reduce false breakouts.

## Entry Rules

1. Define opening range high (ORH) and opening range low (ORL) between 09:30 and 09:45 ET.
2. On the first 5-minute close above ORH, enter long if a bullish FVG is present on the same bar.
3. On the first 5-minute close below ORL, enter short if a bearish FVG is present on the same bar.
4. Bullish FVG condition: Candle[i-2].low > Candle[i-1].high.
5. Bearish FVG condition: Candle[i-2].high < Candle[i-1].low.
6. Only one entry per session.

## Exit Rules

- Long exit:
  - If price closes back below the most recent swing low after entry.
  - If price fills the FVG zone and closes inside it.
  - Optional session close exit at 16:00 ET.
- Short exit:
  - If price closes back above the most recent swing high after entry.
  - If price fills the FVG zone and closes inside it.
  - Optional session close exit at 16:00 ET.

## Stop Loss Rules

- Long initial stop:
  - 1 x 5-minute ATR(14) below entry, or below ORL, whichever is lower.
- Short initial stop:
  - 1 x 5-minute ATR(14) above entry, or above ORH, whichever is higher.
- Stop volatility adjustment:
  - If ATR is more than 1.5x its 20-session average, widen stop to 1.5 x ATR.

## Profit Target Rules

- Base target:
  - 1.5 x initial risk (R-multiple).
- Trailing behavior:
  - When trade reaches 1R, move stop to breakeven.
  - When trade reaches 1.5R, allow 1 x ATR trailing stop from that point.
- No more than two profit targets per strategy specification without explicit override.

## Risk Rules

- Maximum risk per trade: 1% of account equity.
- Position size determined by stop distance and MNQ tick value ($0.50 per tick).
- No re-entry after stop or target hit in the same session.
- Maximum one open MNQ position at a time.
- If opening range equals ORH = ORL, skip the session; do not trade.

## Filters

- Volatility filter:
  - ATR(14) must be above minimum threshold; otherwise gap edges are stale.
- Time filter:
  - No entries within 5 minutes of high-impact news events if applied.
- Session filter:
  - Only regular RTH session; avoid overnight gaps in this spec.

## Regime Assumptions

- Best suited to trending or volatile morning sessions.
- Tends to underperform in low-volatility, range-bound opens.
- Must be evaluated separately in:
  - Bull regime
  - Bear regime
  - Sideways regime
  - Transition regime
- Regime analysis should use:
  - Markov transition matrix
  - Hidden Markov Model
- Expected outcome: best performance in trending regimes; degradation in sideways/transition.

## Backtest Requirements

- Data source: CME-quality 5-minute MNQ continuous or defined session data.
- Data length: minimum 2 years of session data.
- Commission/slippage:
  - Include realistic futures commission.
  - Include slippage model consistent with CME liquidity.
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

- OR duration: 10–20 minutes in 5-minute steps.
- ATR period: 10–20 in 2-unit steps.
- Risk multiplier (R): 1.2–2.0 in 0.1 steps.
- FVG size filter: 0.5–2.0 x ATR in 0.25 steps.
- Maximum OR width filter: 1.0–3.0 points in 0.25 steps.
- Break-even activation: 0.8–1.5R in 0.1 steps.

## Approval Criteria

- Profit factor: >= 1.20.
- Expectancy: positive.
- Max drawdown: <= 25%.
- Total trades: >= 30.
- No severe walk-forward OOS degradation (configurable threshold; default > 30% drop).
- Monte Carlo survival rate >= 85%.
- Stable parameter zones across optimization.
- Clearly documented best and worst regimes.

## Failure Conditions

- Opening range expands well beyond 1.5x average OR width.
- FVG size is extreme (>3 x ATR).
- Session volume below 10-session average.
- Stop-loss frequency exceeds 70% over any 20-trade window.
- Performance collapses in low-volatility regimes.
