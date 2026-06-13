#!/usr/bin/env python3
"""Run the pipeline demo and write report."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_ROOT = PROJECT_ROOT / "research"

if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from pipeline_demo.pipeline_demo import save_report  # noqa: E402


def main() -> None:
    result = save_report()
    score = result["score"]
    print(result["report"])
    print(f"FINAL_SCORE_SCORE={score.composite_score}")
    print(f"FINAL_SCORE_GRADE={score.grade}")
    print(f"FINAL_SCORE_RECOMMENDATION={score.recommendation}")


if __name__ == "__main__":
    main()
