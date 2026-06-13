# Market Regime Engine

Classification engine for market regimes with Markov and HMM support.

## Purpose
Detect and label market regimes for strategy evaluation and filtering.
Support the existing 4 strategy specs before live-market integration.

## Regimes
- Bull
- Bear
- Sideways / Range
- Transition

## Architecture
- `regime_engine.py`: main module (classifier, 20-day return model, reporting)
- `requirements.txt`: Python dependencies
