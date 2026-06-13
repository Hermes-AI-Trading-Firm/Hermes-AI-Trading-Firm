# Walk-Forward Report — rolling

## Summary
- Mode: rolling
- Windows: 8
- Passed: 0
- Failed: 8
- Overall: FAIL

## Windows
| Window | Train | Test | In-sample | Out-of-sample | Degradation | Pass |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 0-40 | 40-60 | 1.8050 | 1.0000 | 0.55 | FAIL |
| 1 | 20-60 | 60-80 | 1.9350 | 0.8250 | 0.43 | FAIL |
| 2 | 40-80 | 80-100 | 1.8250 | 0.8900 | 0.49 | FAIL |
| 3 | 60-100 | 100-120 | 1.7150 | 0.9500 | 0.55 | FAIL |
| 4 | 80-120 | 120-140 | 1.8400 | 0.7750 | 0.42 | FAIL |
| 5 | 100-140 | 140-160 | 1.7250 | 0.8400 | 0.49 | FAIL |
| 6 | 120-160 | 160-180 | 1.6150 | 0.9050 | 0.56 | FAIL |
| 7 | 140-180 | 180-200 | 1.7450 | 0.7300 | 0.42 | FAIL |

## Failed Windows
0, 1, 2, 3, 4, 5, 6, 7

## Strongest Window
0

## Weakest Window
7

## Notes
- Do not use out-of-sample results to re-select parameters.
- Future leakage checks enforced by validate_no_future_leakage().