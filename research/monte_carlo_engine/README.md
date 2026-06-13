# Monte Carlo Testing Engine

Stress-tests strategy equity paths under randomized trade order,
slippage, commissions, missed trades, and worse fills.

## Purpose
Estimate robustness and survival probability before approval.

## Modes
- Bootstrap resampling
- Slippage stress
- Commission stress
- Missed trade simulation
- Worse-fill scenarios

## Outputs
- Median / best / worst / 5th / 95th percentile ending equity
- Probability of loss
- Drawdown breach probability
- Longest losing streak distribution
- Risk-of-ruin estimate
- Prop-firm style drawdown limits
