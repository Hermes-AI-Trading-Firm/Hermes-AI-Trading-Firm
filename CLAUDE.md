# Hermes AI Trading Firm — Project Governance

## Project Mission

This repository governs a standalone AI-driven quantitative trading research pipeline operated by Hermes Agent. Its purpose is to systematically generate, test, optimize, validate, and archive trading strategies across multiple asset classes with strict risk controls and explainability standards.

## Core Operating Principles

1. **No Unverified Claims**: Never claim profitability without an actual backtest or forward test. Never invent results.
2. **Robustness Over Performance**: A strategy must be explainable, stable, and regime-aware before approval.
3. **Reject, Don't Delete**: Failed strategies are archived under `research/rejected/` with documented reasons. They inform the AI Learning Brain.
4. **Human-in-the-Loop**: No strategy advances from research to live trading without explicit human approval.
5. **Controlled Cadence**: Research and testing operate on a regulated schedule, not continuous uncontrolled loops.

---

## Market Selection Desk

The Market Selection Desk identifies candidate markets across all tradeable asset classes:

### Supported Markets
- **Stocks**: Equities, ETFs, sector-focused instruments
- **Futures**: Index futures (e.g., MNQ, NQ, ES), commodity futures, rate futures
- **Options**: Equity options, index options, weeklys, monthlys
- **Crypto**: Spot, futures, major and altcoin pairs
- **All Markets**: Cross-asset review for relative value, correlation, and macro themes

### Responsibilities
1. Continuously scan for markets exhibiting:
   - Strong trends or clear range-bound behavior
   - Volatility regimes conducive to specific strategy types
   - High liquidity with reasonable transaction cost profiles
   - Session-based patterns (Asian, European, US overlap)
2. Classify each identified market by current state:
   - Trending (directional bias)
   - Sideways/Range-bound (mean-reverting opportunities)
   - Volatile (breakout or volatility-based strategies)
   - Mean-reverting (pairs, arbitrage, statistical)
3. Recommend which strategy types are suitable for each market:
   - Breakouts / Trend following
   - Mean reversion / Statistical arbitrage
   - VWAP pullbacks / Fair value gaps
   - Liquidity sweeps / Stop hunts
   - Session-based strategies (open/close, overlap windows)
   - Options strategies (defined risk, income, volatility)
4. Maintain a candidate watchlist that feeds Strategy Factory
5. Document market-specific constraints: tick size, margin, session hours, corporate actions

### Output Format
For each candidate:
- Market name and asset class
- Symbol or representative ticker
- Current regime classification
- Recommended strategy types
- Suggested timeframe(s) and session
- Confidence score or interest ranking
- Any data availability concerns

---

## Department Directory

| Department | Location | Purpose |
|------------|----------|---------|
| CEO / Command Center | `agents/ceo/` | Orchestrate workflow, assign tasks, approve/reject, enforce compliance |
| Market Selection Desk | `agents/market_selection_desk/` | Identify tradeable markets across stocks, futures, options, crypto |
| Strategy Factory | `agents/strategy_factory/` | Generate new trading strategy ideas |
| Market Research | `agents/market_research/` | Research market microstructure, correlations, and conditions |
| Quant Research | `agents/quant_research/` | Convert ideas into precise, executable strategy specifications |
| Strategy Coding Desk | `agents/strategy_coding_desk/` | Implement PineScript / code from specifications |
| Backtesting Lab | `agents/backtesting_lab/` | Execute backtests and export real metrics |
| Optimization Lab | `agents/optimization_lab/` | Optimize parameters only after baseline validation |
| Risk Department | `agents/risk_department/` | Evaluate and gate strategies on risk metrics |
| Market Regime Lab | `agents/regime_lab/` | Analyze strategy performance by market regime |
| AI Learning Brain | `agents/ai_learning_brain/` | Maintain pattern library of what works and fails |
| Forward Testing Journal | `agents/forward_testing/` | Track paper trades for approved strategies |
| Dashboard | `agents/dashboard/` | Aggregate firm metrics and status |
| Approved Strategies | `research/approved/` | Archive of approved strategy artifacts |
| Rejected Strategies | `research/rejected/` | Archive of failed strategies with reasons |

---

## Canonical Workflow

```
Market Selection Desk
  → Market Research
    → Strategy Factory
      → Quant Research
        → Strategy Coding Desk
          → Backtesting Lab
            → Risk Department
              → Market Regime Lab
                → Optimization Lab
                  → Walk-Forward Testing
                    → Monte Carlo Testing
                      → AI Learning Brain
                        → Dashboard
                          → Human Approval Gate
                            → Forward Testing Journal
                              → Approved Strategies
                              OR
                              → Rejected Strategies
```

### Workflow Rules

- Each department must produce artifacts on disk before passing to the next.
- Every strategy must be traceable through the full pipeline.
- Rejected strategies are never removed; they are archived with documentation.

---

## Testing & Validation Gates

### 1. Baseline Backtest
Before any optimization, a strategy must demonstrate:
- Profit factor > 1.20
- Positive expectancy
- Minimum trade count (configurable, default ≥ 30 trades)
- Acceptable maximum drawdown (configurable, default ≤ 25%)
- Clear, non-repainting rules

### 2. Market Regime Analysis
Every strategy must be evaluated across:
- **Bull regime**: upward trending, positive momentum
- **Bear regime**: downward trending, negative momentum
- **Sideways regime**: range-bound, low directional bias
- **Transition regime**: changing between regimes, ambiguous state

Apply:
- **Markov Transition Matrix**: estimate regime persistence and transition probabilities between states.
- **Hidden Markov Models (HMM)**: infer latent regimes from price, volatility, and volume data when explicit labels are unavailable.

Decision rule: If a strategy performs only in one regime, document that constraint. Do not approve cross-regime claims without evidence.

### 3. Walk-Forward Testing
- Use expanding or rolling windows.
- Re-optimize periodically on in-sample data.
- Validate on out-of-sample data.
- Compare in-sample vs out-of-sample performance degradation.
- Reject strategies with severe degradation (configurable threshold).

### 4. Monte Carlo Testing
- Randomize trade order.
- Simulate varying slippage and commission profiles.
- Test worst-case drawdown sequences.
- Require minimum survival rate (configurable, default ≥ 85% of trials reach final equity without ruin).

### 5. Human Approval Gate
- Final approval requires human sign-off.
- Hermes may research, test, and rank, but cannot authorize live deployment.
- Approval artifacts must be logged under `research/approved/`.

---

## Operating Cadence

| Frequency | Activity |
|-----------|----------|
| **Daily** | Up to 3 new strategy ideas from Strategy Factory; backtest 1–3 completed specs |
| **Weekly** | Review top 10 strategies by rank; optimize best 3 |
| **Monthly** | Comprehensive review of all approved strategies; retire weak performers; select forward-test candidates; human review session; update AI Learning Brain insights |

---

## Directory Specifications

### Research Directories
| Path | Purpose |
|------|---------|
| `research/strategy_queue/` | Active ideas and specs awaiting processing |
| `research/approved/` | Fully vetted, approved strategies |
| `research/rejected/` | Failed strategies with reason files |
| `research/forward_testing/` | Paper-trading journals |
| `research/specs/` | Completed Quant Research specs |

### Agent Directories
| Path | Purpose |
|------|---------|
| `agents/ceo/` | CEO prompts and orchestration rules |
| `agents/market_selection_desk/` | Market scanning prompts |
| `agents/strategy_factory/` | Idea generation prompts |
| `agents/market_research/` | Market analysis prompts |
| `agents/quant_research/` | Specification prompts |
| `agents/strategy_coding_desk/` | Code generation prompts |
| `agents/backtesting_lab/` | Backtest execution prompts |
| `agents/optimization_lab/` | Optimization prompts |
| `agents/risk_department/` | Risk review prompts |
| `agents/regime_lab/` | Regime analysis prompts |
| `agents/ai_learning_brain/` | Learning brain prompts |
| `agents/forward_testing/` | Forward test prompts |
| `agents/dashboard/` | Dashboard prompts |

### Data Directories
| Path | Purpose |
|------|---------|
| `database/` | SQLite / CSV / JSON strategy records |
| `logs/` | Execution logs, errors, audit trail |
| `reports/` | Markdown / HTML dashboards and summaries |

---

## Strategy Metadata Standards

Every strategy artifact must include:
- Strategy ID (unique, immutable)
- Name and description
- Asset class, symbol, timeframe, session
- Entry rules (precise, code-compatible)
- Exit rules (stop loss, profit target, trailing rules)
- Risk rules: position sizing, max drawdown limit, exposure rules
- Filters: indicators, regime constraints, time filters
- Optimization variables: parameter ranges
- Backtest results: metrics, equity curve, trade list
- Regime analysis: per-regime performance
- Walk-forward results: IS vs OOS comparison
- Monte Carlo results: survival rate, drawdown distribution
- Risk review: approval/rejection decision with rationale
- Outcome: pending, approved, rejected, archived, retired
- Timestamps for each stage transition

---

## Constraints & Prohibitions

### Hard Prohibitions
1. **Do not invent results.** All metrics must originate from actual backtests.
2. **Do not approve from optimization alone.** Baseline validation is mandatory.
3. **Do not delete rejected strategies.** Archive with full context.
4. **Do not live trade without human approval.** This gate is non-negotiable.
5. **Do not claim cross-regime robustness without evidence.** Document regime-specific performance.

### Mandatory Actions
1. Always update `database/`, `reports/`, and `agents/ai_learning_brain/` after each result.
2. Always compare optimized vs baseline performance.
3. Always explain whether a strategy is robust or overfit.
4. Always document rejection reasons in `research/rejected/`.
5. Always enforce the human approval gate before forward testing or live deployment.

---

## Risk Thresholds (Defaults)

| Metric | Threshold |
|--------|-----------|
| Profit Factor | ≥ 1.20 |
| Expectancy | Positive |
| Max Drawdown | ≤ 25% (configurable per asset class) |
| Minimum Trades | ≥ 30 |
| Overfit Warning | OOS performance drop > 30% from baseline |
| Monte Carlo Survival | ≥ 85% |

Thresholds may be tightened for specific asset classes but never relaxed without human approval.

---

## AI Learning Brain Requirements

The Learning Brain must maintain:
- Pattern database: indicators, strategy types, asset classes, timeframes
- Success patterns: combinations with positive outcomes
- Failure patterns: combinations that consistently fail
- Overfit signatures: parameter sensitivity, regime collapse, OOS decay
- Regime-coupled performance: which strategies work in which regimes
- Optimization history: parameter stability notes

Insights must be surfaced:
- After each strategy completion
- Weekly summary to CEO
- Monthly comprehensive pattern review

---

## Approval Categories

1. **Approved**: Passed all gates; eligible for forward testing.
2. **Rejected**: Failed baseline or risk review; archived with reason.
3. **Needs Improvement**: Returned to Quant Research with specific fixes.
4. **Optimization Candidate**: Passed baseline and regime analysis; sent to Optimization Lab.
5. **Forward Test Candidate**: Passed optimization, walk-forward, Monte Carlo, and human approval.
6. **Retired**: Previously approved but removed from active consideration (monthly review).

---

## Human Approval Workflow

1. CEO compiles approval package:
   - Backtest results
   - Regime analysis
   - Walk-forward metrics
   - Monte Carlo results
   - Risk review summary
   - AI Learning Brain perspective
2. Human reviews package.
3. Human approves, rejects, or requests modifications.
4. Decision is recorded in `research/approved/` or `research/rejected/`.
5. Only approved strategies enter `research/forward_testing/`.

---

## Future Extensions

- Add `agents/trader_integration/` for broker/exchange connectivity.
- Add `agents/execution_engine/` for order management.
- Add `agents/compliance/` for regulatory checks.
- Expand Market Selection Desk to include fundamental and alternative data signals.

This CLAUDE.md supersedes ad-hoc decisions. All future project behavior must align with this document.
