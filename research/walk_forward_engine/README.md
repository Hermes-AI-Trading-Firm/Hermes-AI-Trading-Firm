# Walk-Forward Testing Engine

Reusable framework for out-of-sample walk-forward validation.

## Purpose
Evaluate whether strategy performance generalizes beyond in-sample data and whether optimization results suffer from future leakage.

## Modes
- Rolling
- Anchored
- Expanding

## Safeguards
- No lookahead bias
- No using test data to select parameters
- Test windows always after training windows
