# Pipeline Demo Report

End-to-end research pipeline integration using synthetic data.

---

# Regime Report

- Bull: count=0, win_rate=None, avg_return=None, sharpe=None, max_drawdown=None
- Bear: count=0, win_rate=None, avg_return=None, sharpe=None, max_drawdown=None
- Sideways: count=38, win_rate=0.3947, avg_return=0.013098, sharpe=9.8593, max_drawdown=-0.0238
- Transition: count=466, win_rate=0.4936, avg_return=0.003777, sharpe=1.2909, max_drawdown=-0.9466

Last regime: Transition

---

# Walk-Forward Report — rolling

## Summary
- Mode: rolling
- Windows: 2
- Passed: 0
- Failed: 2
- Overall: FAIL

## Windows
| Window | Train | Test | In-sample | Out-of-sample | Degradation | Pass |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 0-60 | 60-80 | 85.8252 | 30.8612 | 0.36 | FAIL |
| 1 | 20-80 | 80-100 | 89.7992 | 30.4535 | 0.34 | FAIL |

## Failed Windows
0, 1

## Strongest Window
0

## Weakest Window
1

## Notes
- Do not use out-of-sample results to re-select parameters.
- Future leakage checks enforced by validate_no_future_leakage().

---

# Monte Carlo Report

## Summary
- Simulations: 1000
- Median ending equity: 163,832.23
- Best ending equity: 180,243.29
- Worst ending equity: 109,867.92
- 5th percentile equity: 153,980.80
- 95th percentile equity: 172,576.71
- Probability of loss: 0.00%
- Drawdown breach probability: 0.00%
- Risk of ruin: 0.30%

## Pass Status
- PASS

## Distribution Notes
- Max drawdown distribution saved in JSON metadata only.
- Longest losing streak max: 10

## Rules Applied
- Bootstrap trade shuffle
- Slippage stress
- Commission stress
- Random missed trades
- Worse-fill stress

## Safeguards
- No lookahead: each simulation uses only base trade sequence.
- No future leakage: parameters fixed before simulation.

---

# Strategy Scoring Report

## Composite
- Score: 85.39
- Grade: A
- Recommendation: Forward Test

## Category Scores
- profitability: 0.6972
- drawdown: 0.9000
- consistency: 0.7000
- walk_forward: 0.0000
- monte_carlo: 1.0000
- regime: 0.2000
- robustness: 0.7000
- prop_firm: 1.0000
- explainability: 0.9000
- overfitting_risk: 0.4000

## Prop-Firm Suitability
- Account size: 100000.0
- Trailing drawdown limit: 0.2
- Supported: Yes
- Drawdown breach probability: 0.0
- Max losing streak: 10

## Overfitting Warnings
- Walk-forward degradation is high.

## Notes
- Demo only. Not based on live backtests.
- Next step: attach real backtest / regime / wf / mc results.