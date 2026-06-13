# Strategy Specification

- **Spec ID**: MNQ_ORB_FVG_v001
- **Status**: draft
- **Created By**: Strategy Factory → Quant Research

## Overview

Opening Range Breakout (ORB) combined with a Fair Value Gap (FVG) continuation play on Micro E-mini Nasdaq-100 futures.

## Market

- Asset Class: futures
- Symbol: MNQ
- Exchange: CME / GLOBEX
- Session: US Regular (09:30–16:00 ET)
- Tick Size: 0.25
- Pip Value: $0.50 per tick

## Timeframe

- Chart: 5-minute (5m)
- Session bar count: first 15 minutes (09:30–09:45 ET) defines opening range

## Entry Rules

1. Define opening range high (ORH) and opening range low (ORL) from 09:30 to 09:45 ET.
2. Wait for a close above ORH or below ORL on the 5-minute bar.
3. Enter long on first 5-minute close above ORH; enter short on first close below ORL.
4. Long entry only if a bullish Fair Value Gap (FVG) exists between the last two 5-minute candles:
   - Candle[i-2].low > Candle[i-1].high
   - Candle[i].close > Candle[i-1].high (breakout confirmation)
5. Short entry only if a bearish FVG exists:
   - Candle[i-2].high < Candle[i-1].low
   - Candle[i].close < Candle[i-1].low

## Exit Rules

- **Long Exit**: Close below the most recent swing low after entry, OR when price returns into the FVG zone and fills it.
- **Short Exit**: Close above the most recent swing high after entry, OR when price returns into the FVG zone and fills it.
- No time-based exit unless combined with session close (see Risk Rules).

## Stop Rules

- **Long Stop**: 1×5-minute ATR (14-period) below entry price, or below ORL if ORL is further away.
- **Short Stop**: 1×5-minute ATR (14-period) above entry price, or above ORH if ORH is further away.
- Stop must be placed beyond the FVG zone to avoid noise-induced exits.

## Target Rules

- **Long Target**: 1.5× risk (R-multiple), measured from entry to initial stop.
- **Short Target**: 1.5× risk (R-multiple), measured from entry to initial stop.
- Trailing activation: if trade moves to 1R, move stop to breakeven.
- Optional trailing: use 1× ATR trailing stop after 1.5R.

## Risk Rules

- Maximum risk per trade: 1% of account equity.
- Position size based on stop distance in ticks × $0.50.
- Only one entry per session; no re-entry after stop or target hit.
- Do not trade within 5 minutes of major news events (NFP, FOMC, CPI) if applied.
- If ORH = ORL (opening range < 1 point on MNQ), do not trade that session.

## Edge Hypothesis

- ORB captures directional conviction after a volatility contraction at the open.
- FVG confirms that a liquidity void exists; price tends to fill voids quickly in high-liquidity index futures.
- Combining ORB with FVG filters out false breakouts by requiring structural imbalance.

## Failure Conditions

- Opening range expands beyond 1.5× average OR width, indicating choppy session start.
- FVG is extremely large (>3× ATR); probability of retest increases but trend continuation decreases.
- Session volume is lower than the 10-session average for the same time of day.
- Stop-loss hit frequency exceeds 70% over any rolling 20-trade window.
- Performance collapses in low-volatility regimes.

## Suggested Optimization Variables

| Parameter | Range | Step |
|-----------|-------|------|
| OR duration (minutes) | 10–20 | 5 |
| ATR period | 10–20 | 2 |
| Risk multiplier (target) | 1.2–2.0 | 0.1 |
| FVG size filter (× ATR) | 0.5–2.0 | 0.25 |
| Max OR width filter (points) | 1.0–3.0 | 0.25 |
| Breakeven activation (R) | 0.8–1.5 | 0.1 |

## Notes

- Requires 5-minute historical data for MNQ with full session.
- Backtest should include commission and slippage appropriate for CME futures.
- Optimization should use expanding walk-forward, not expanding in-sample optimization.
- Regime analysis should separate trending vs sideways morning sessions.
