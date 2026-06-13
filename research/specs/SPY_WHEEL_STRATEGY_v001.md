# Complete Strategy Specification

- **Spec ID**: SPY_WHEEL_STRATEGY_v001
- **Status**: specification-complete
- **Created By**: Quant Research

## Strategy

- **Strategy Name**: SPY Options Wheel Income Strategy
- **Market**: Options
- **Symbol**: SPY
- **Timeframe**: Daily close for signals; options evaluated on 21–45 day DTE cycles.
- **Session**: US Regular Hours underlying close; options trade through 16:15 ET standard monthlies.
- **Strategy Type**: Defined-Risk Options Income / Wheel

## Overview

Premium-collection strategy selling SPY puts in low-IV, uptrending environments, and selling covered calls when assigned. Designed for capital-efficient income generation with capital backing constraints.

## Entry Rules

1. Regime and volatility filter:
   - Only enter when 20-day realized volatility is in the lowest 40% of the past 2 years.
   - SPY price at or above 20-period daily SMA.
   - IV rank below 40%.
2. Put selling:
   - Sell 1 put at 0.15–0.25 delta below ATM.
   - Target 21–45 DTE.
3. Call selling after assignment:
   - If short put is assigned into 100 shares, sell 1 call at 0.15–0.25 delta above current price.
   - Target 21–45 DTE.
4. Roll rules:
   - If put or call becomes ITM beyond delta thresholds with <= 7 DTE, roll up-and-out or down-and-out to next 21–45 DTE cycle.

## Exit Rules

- Put exit:
  - Close when premium decays to <= 50% of credit received, OR at <= 21 DTE, whichever occurs first.
- Call exit:
  - Close when premium decays to <= 50% of credit received, OR at <= 21 DTE, whichever occurs first.
- Stress exit:
  - If SPY drops >= 10% below assigned share cost basis, close shares and suspend new option writing until volatility mean-reverts.
- Volatility spike exit:
  - If realized volatility spikes above 90th percentile of 2-year history, flatten all positions.

## Stop Loss Rules

- Options positions have defined max loss by strike selection; no separate stop on options legs.
- Short stock risk management:
  - Trailing stop at 10% below highest price since assignment or since last option cycle.
- Liquidity halt rule:
  - If net liquidity drops below 10% of equity, halt new entries.

## Profit Target Rules

- Profit target is premium decay rather than directional gain.
- Target realized as 50% credit or by expiration, whichever comes first.
- Strategy does not target full directional moves; upside capped by short calls.

## Risk Rules

- Max total capital allocation: 30% of account equity reserved for potential short assignment backing.
- Max individual trade risk: 2% of equity.
- Max open options positions: 6 total (3 puts, 3 calls).
- Margin check after each assignment; respect Reg T overnight margin requirements.
- Avoid writing earnings-linked options within 7 days of major SPY earnings season risk windows.
- Do not write options when bid-ask spread > 20% of mid-price for the chosen strikes.

## Filters

- Volatility regime filter:
  - Realized volatility must be in lowest 40%.
- Trend filter:
  - SPY above 20-day SMA.
- IV rank filter:
  - IV rank < 40%.
- Liquidity filter:
  - Bid-ask width < 20% of mid-price.
- Earnings window filter:
  - No earnings-sensitive writing.

## Regime Assumptions

- Best suited to:
  - Low-implied-volatility environments.
  - Environment with tendency for IV mean reversion.
- Worst environments:
  - Volatility expansions after shocks.
  - Strong directional bull markets where calls get assigned early.
- Must document performance separately by:
  - Low-IV / high-IV
  - Earnings-season / non-earnings-season
  - Trending / mean-reverting equity regimes

## Backtest Requirements

- Data source:
  - Historical end-of-day SPY options chain or synthetic IV history.
  - Underlying daily prices.
- Data length: minimum 2 years.
- Assumptions:
  - Realistic commissions, fees, and assignment model.
  - Realistic fill assumptions for limit orders near mid-price.
- Minimum acceptable trade count: 30 contracts / assignments.
- Report:
  - Net profit
  - Profit factor
  - Win rate
  - Max drawdown
  - Max margin utilization
  - Average premium captured
  - Recovery factor
  - Sharpe ratio
  - Trade list with regime tags

## Suggested Optimization Variables

- SMA period: 15–30 days in 5-day steps.
- Put delta target: 0.10–0.30 in 0.05 steps.
- Call delta target: 0.10–0.30 in 0.05 steps.
- DTE target: 14–60 days in 7-day steps.
- Max portfolio allocation: 20–40% in 5% steps.
- Close at profit threshold: 40–70% in 10% steps.
- Realized volatility percentile filter: 10–60 in 10-percent steps.

## Approval Criteria

- Profit factor: >= 1.20.
- Expectancy: positive.
- Max drawdown: <= 25%.
- Trade count: >= 30.
- Walk-forward OOS degradation not greater than 30%.
- Monte Carlo survival rate >= 85%.
- Stable parameter zones across optimization.
- Documented regime-specific performance.

## Failure Conditions

- IV spikes unexpectedly.
- Assignment in low-liquidity strikes.
- Low-IV followed by volatility expansion with mark-to-market losses.
- Rolling costs erode premium advantage in trending markets.
- Strong bull markets where call assignment caps upside.
