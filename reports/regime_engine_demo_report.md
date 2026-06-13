# Market Regime Engine — Demo Report

## Input
- Series: synthetic bull+bear (n=504)

## Rule-Based Regime Performance
# Regime Report

- Bull: count=0, win_rate=None, avg_return=None, sharpe=None, max_drawdown=None
- Bear: count=0, win_rate=None, avg_return=None, sharpe=None, max_drawdown=None
- Sideways: count=38, win_rate=0.1053, avg_return=-0.009557, sharpe=-8.8903, max_drawdown=-0.3374
- Transition: count=466, win_rate=0.3348, avg_return=-0.016637, sharpe=-5.8449, max_drawdown=-1.0

Last regime: Transition

## Markov Transition Matrix
| From / To | Bull | Bear | Sideways | Transition |
|---|---|---|---|---|
| Bull | 0.00 | 0.00 | 0.00 | 0.00 |
| Bear | 0.00 | 0.00 | 0.00 | 0.00 |
| Sideways | 0.00 | 0.00 | 0.97 | 0.03 |
| Transition | 0.00 | 0.00 | 0.00 | 1.00 |

## Hidden Markov Model
- States used: 4
- Model: GaussianHMM
- Predictions sample: [2, 2, 2, 2, 2, 2, 2, 2, 2, 2]

## Notes
- Do not treat demo synthetic results as live signal.
- Next step: attach OHLCV data for the 4 queued strategies.