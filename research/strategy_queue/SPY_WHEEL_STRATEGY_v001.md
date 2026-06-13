# Strategy Specification

- **Spec ID**: SPY_WHEEL_STRATEGY_v001
- **Status**: draft
- **Created By**: Strategy Factory → Quant Research

## Overview

Defined-risk "wheel" style strategy on SPY using options. Collect premium by selling puts in neutral-to-bullish regimes, and selling covered calls if assigned. Designed for income generation with capital efficiency.

## Market

- Asset Class: options
- Symbol: SPY
- Exchange: CBOE / Listed Options
- Session: US Regular (RTH) for underlying; options trade until 16:15 ET for standard monthlies, later for weeklies.
- Tick Size: 0.01
- Contract Multiplier: 100

## Timeframe

- Underlying chart: daily close for regime and signal assessment.
- Option horizon: 21–45 days to expiration (DTE).

## Entry Rules

1. Regime filter: only enter when SPY 20-day realized volatility is in the lowest 40% of the past 2 years.
2. Sell 1 put contract at 0.15–0.25 delta below at-the-money (ATM) when:
   - Underlying price is at or above 20-period SMA on daily chart.
   - IV rank < 40% (1-year IV history).
3. If assigned (short put exercised into long 100 shares of SPY):
   - Sell 1 call contract at 0.15–0.25 delta above current price.
   - Target 21–45 DTE.

## Exit Rules

- **Put Exit**: Buy back for <= 50% of credit received, OR close at 21 DTE whichever comes first.
- **Call Exit**: Buy back for <= 50% of credit received after assignment, OR close at 21 DTE whichever comes first.
- **Stress Exit**: If SPY drops >= 10% below assigned share cost basis, close shares and suspend new option writing until volatility mean-reverts.
- If realized volatility spikes above 90th percentile, flatten all positions.

## Stop Rules

- No stop on the options spreads themselves; risk is defined by strike selection.
- Underlying equity risk stop for short stock: trailing stop 10% below highest price since assignment or since last option cycle.
- If net liquidity drops below 10% of equity, halt new entries.

## Target Rules

- **Put Profit Target**: close position at 50% credit received or at 21 DTE, whichever comes first.
- **Call Profit Target**: close position at 50% credit received or at 21 DTE, whichever comes first.
- Target profit is defined by the premium structure, not by directional move.

## Risk Rules

- Max capital allocated: 30% of account equity used as backing for short assignments.
- Max individual trade risk: 2% of equity.
- Max open options positions: 6 at any time (3 puts, 3 calls).
- Margin requirement check after each assignment; do not exceed Reg T overnight requirements.
- Do not write earnings-linked options within 7 days of SPY-component earnings season peaks.
- Do not write options if bid-ask spread > 20% of mid-price for the chosen strikes.

## Edge Hypothesis

- Selling premium in low-implied-volatility environments tends to outperform because mean reversion of IV is strong.
- Selling puts below a rising SMA biases the trade in the direction of the underlying trend while collecting income.
- Wheel structure recycles capital: puts convert to stock assignments which then generate call premium, potentially compounding income.

## Failure Conditions

- IV spikes unexpectedly due to macro shocks; short options move against the position faster than premium decay.
- Assignment in low-liquidity strikes increases slippage and execution risk.
- Low-IV environment followed by a volatility expansion causes mark-to-market losses even if trade eventually profits.
- Rolling costs erode premium advantage in trending markets.
- Strategy underperforms in strong bull markets where calls assigned too early and upside is capped.

## Suggested Optimization Variables

| Parameter | Range | Step |
|-----------|-------|------|
| SMA period | 15–30 | 5 |
| Delta target (put) | 0.10–0.30 | 0.05 |
| Delta target (call) | 0.10–0.30 | 0.05 |
| DTE target | 14–60 | 7 |
| Max portfolio allocation (%) | 20–40 | 5 |
| Close at profit (%) | 40–70 | 10 |
| Realized vol percentile filter | 10–60 | 10 |

## Notes

- Requires equity options data chain; backtesting can use end-of-day synthetic IV or live option pricing history if available.
- Commissions, fees, and assignment risk must be modeled with realistic fills.
- Regime analysis should separate "earnings season" vs "non-earnings season" behavior.
- Forward-test using a paper-trading brokerage environment that models option fills accurately.
