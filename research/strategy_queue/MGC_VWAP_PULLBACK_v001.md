# Strategy Specification

- **Spec ID**: MGC_VWAP_PULLBACK_v001
- **Status**: draft
- **Created By**: Strategy Factory → Quant Research

## Overview

VWAP pullback strategy on Micro Gold futures (MGC). Trades intraday pullbacks to VWAP in the direction of the prevailing session trend.

## Market

- Asset Class: futures
- Symbol: MGC
- Exchange: CME / GLOBEX
- Session: COMEX Gold session (approx. 08:20–13:30 ET, plus some electronic access)
- Tick Size: 0.10
- Pip Value: $0.10 per tick (1.00 = $10)

## Timeframe

- Chart: 3-minute (3m)
- Session alignment: full electronic session or defined gold session window

## Entry Rules

1. Calculate VWAP and 1× standard deviation bands (VWAP ± 1σ) from session start.
2. Determine session trend bias:
   - Bullish bias if price above VWAP for the first 30 minutes of the session.
   - Bearish bias if price below VWAP for the first 30 minutes of the session.
3. Long Entry:
   - Price pulls back to touch or cross below the lower VWAP band (VWAP - 1σ).
   - On the same 3-minute bar, close returns above VWAP.
   - Enter on next 3-minute bar open.
4. Short Entry:
   - Price pulls back to touch or cross above the upper VWAP band (VWAP + 1σ).
   - On the same 3-minute bar, close returns below VWAP.
   - Enter on next 3-minute bar open.

## Exit Rules

- **Long Exit**: Close below VWAP - 1σ after entry, OR close below session VWAP.
- **Short Exit**: Close above VWAP + 1σ after entry, OR close above session VWAP.
- Time exit: flat 5 minutes before session end to avoid overnight risk.
- Exit immediately if COMEX circuit breaker thresholds are breached (if available).

## Stop Rules

- **Long Stop**: Below the pullback swing low by 1 tick, or at least 0.50 points below entry.
- **Short Stop**: Above the pullback swing high by 1 tick, or at least 0.50 points above entry.
- If stop distance would exceed 1.5% of MGC notional value, reduce position size or skip trade.

## Target Rules

- **Long Target**: VWAP + 0.5σ (first target), remainder target VWAP + 1.5σ (runner).
- **Short Target**: VWAP - 0.5σ (first target), remainder target VWAP - 1.5σ (runner).
- Scale out: 50% at first target, 50% at runner.
- If runner is not reached within 6 bars of first target, move runner stop to breakeven.

## Risk Rules

- Max risk per trade: 0.75% of account equity.
- No overlapping trades; only one position at a time.
- Do not trade during NFP or Fed announcements in the gold session.
- Avoid trading within the first 5 minutes after session open; wait for VWAP stabilization.
- If VWAP band width is < 5 ticks for 3 consecutive bars, skip trade (band too tight, low edge).

## Edge Hypothesis

- VWAP is a widely followed institutional benchmark; mean-reversion to VWAP after a pullback is statistically probable in liquid futures.
- Gold futures exhibit strong intraday trendiness; VWAP acts as dynamic support/resistance.
- Using ±1σ bands increases probability of a recoil because extremes are mean-reverting in low-volatility gold sessions.

## Failure Conditions

- Strong directional news moves that push price away from VWAP without retracing.
- Low-volume Asian overlap sessions where VWAP is unreliable.
- Band compression followed by explosive volatility (failed mean-reversion).
- Win rate drops below 55% over 20-trade window.
- Max drawdown exceeds 15% intraday on any given session.

## Suggested Optimization Variables

| Parameter | Range | Step |
|-----------|-------|------|
| Trend detection window (minutes) | 20–40 | 5 |
| VWAP band multiplier (σ) | 0.75–1.5 | 0.25 |
| Timeframe | 2m, 3m, 5m | step 1m |
| First target (σ from VWAP) | 0.25–1.0 | 0.25 |
| Runner target (σ from VWAP) | 1.0–2.5 | 0.25 |
| Max hold bars | 20–60 | 5 |
| Band width filter (ticks) | 5–15 | 2.5 |

## Notes

- Backtest must include full COMEX session data; partial-day data invalidates VWAP accuracy.
- Consider weekend gap risk; MGC opens Sunday evening ET.
- Regime analysis should separate gold-specific risk-on/risk-off days.
