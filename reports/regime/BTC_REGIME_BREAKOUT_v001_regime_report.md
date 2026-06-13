# BTC_REGIME_BREAKOUT_v001 Regime Report

## Strategy
- Spec ID: BTC_REGIME_BREAKOUT_v001
- Symbol: BTCUSDT
- Asset class: crypto
- Timeframe: daily
- Session: 24h UTC

## Current Regime
- Transition

## Regime Counts
- Bull: 0
- Bear: 0
- Sideways: 0
- Transition: 20

## Markov Transition Matrix
| From / To | Bull | Bear | Sideways | Transition |
| --- | --- | --- | --- | --- |
| Bull | 0.00 | 0.00 | 0.00 | 0.00 |
| Bear | 0.00 | 0.00 | 0.00 | 0.00 |
| Sideways | 0.00 | 0.00 | 0.00 | 0.00 |
| Transition | 0.00 | 0.00 | 0.00 | 1.00 |

## Stickiness Score
- 0.25

## HMM Regime Summary
- Model: lightweight rule-based surrogate (fallback GaussianHMM not installed).
- Inference: Falling back to rule-based states.

## Interpretation
- Current regime: Transition.
- Review the counts and transition matrix above to assess persistence.
- High transition probability into Transition indicates choppy or changing conditions.

## Regime Filtering Recommendation
- Recommended to test regime filtering first because Transition count is present in the stub dataset.