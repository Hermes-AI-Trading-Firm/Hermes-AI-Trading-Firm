# Monte Carlo Report

## Summary
- Simulations: 1000
- Median ending equity: 87,281.66
- Best ending equity: 103,210.60
- Worst ending equity: 44,249.25
- 5th percentile equity: 78,533.23
- 95th percentile equity: 94,220.05
- Probability of loss: 0.70%
- Drawdown breach probability: 0.90%
- Risk of ruin: 3.60%

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