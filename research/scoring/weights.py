"""Scoring weights, thresholds, and classification bands.

All tuning lives here — scoring.py reads these constants and never
hard-codes numbers.  Change these to tighten or loosen the engine
without touching evaluation logic.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Component weights — must sum to 1.0
# ---------------------------------------------------------------------------

SCORING_WEIGHTS: Dict[str, float] = {
    "profitability":  0.30,
    "drawdown":       0.20,
    "consistency":    0.15,
    "regime":         0.10,
    "monte_carlo":    0.10,
    "walk_forward":   0.05,
    "robustness":     0.05,
    "prop_firm":      0.03,
    "explainability": 0.02,
}
# sum == 1.00

# ---------------------------------------------------------------------------
# Hard-gate thresholds — failing any gate forces grade → Reject
# ---------------------------------------------------------------------------

THRESHOLDS: Dict[str, float] = {
    "min_profit_factor": 1.20,
    "max_drawdown_pct":  0.25,   # absolute value, e.g. 0.25 = 25 %
    "min_trades":        30,
    "min_mc_survival":   0.85,
    "min_expectancy":    0.0,
}

# ---------------------------------------------------------------------------
# Grade bands — (grade_label, minimum_composite_score)
# Evaluated top-to-bottom; first match wins.
# ---------------------------------------------------------------------------

GRADE_BANDS: List[Tuple[str, float]] = [
    ("A+", 90.0),   # 90–100
    ("A",  80.0),   # 80–89
    ("B",  70.0),   # 70–79
    ("C",  60.0),   # 60–69
    ("D",   0.0),   # < 60
]

# ---------------------------------------------------------------------------
# Recommendation map — grade → pipeline action
# ---------------------------------------------------------------------------

RECOMMENDATION_MAP: Dict[str, str] = {
    "A+":     "Live Candidate",
    "A":      "Forward Test",
    "B":      "Optimize",
    "C":      "Retest",
    "D":      "Reject",
    "Reject": "Reject",   # hard-gate override
}
