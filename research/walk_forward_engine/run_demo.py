#!/usr/bin/env python3
"""Generate synthetic scores and run walk-forward demo for reporting."""

from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from walk_forward_engine import WindowMode, render_report, run_walk_forward

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = PROJECT_ROOT / "reports" / "walk_forward" / "walk_forward_demo_report.md"
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    n = 200
    scores = [0.02 + (i % 12) * 0.005 - 0.001 * (i // 25) for i in range(n)]
    report = run_walk_forward(scores, train_size=40, test_size=20, mode=WindowMode.ROLLING)
    text = render_report(report)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(REPORT_PATH)
    print(text)


if __name__ == "__main__":
    main()
