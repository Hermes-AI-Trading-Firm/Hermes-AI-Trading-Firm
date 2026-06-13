"""Walk-Forward Testing Engine

Validates out-of-sample generalization and guards against future leakage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence

# --------------------
# Data structures
# --------------------


class WindowMode(str, Enum):
    ROLLING = "rolling"
    ANCHORED = "anchored"
    EXPANDING = "expanding"


@dataclass(frozen=True)
class Window:
    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class WindowResult:
    window: Window
    in_sample_score: float
    out_of_sample_score: float
    degradation_ratio: float = field(init=False)
    passed: bool = field(init=False)

    def __post_init__(self) -> None:
        if self.in_sample_score <= 0:
            degradation = float("inf")
            passed = False
        else:
            degradation = self.out_of_sample_score / self.in_sample_score
            passed = degradation >= 0.7
        object.__setattr__(self, "degradation_ratio", round(float(degradation), 4))
        object.__setattr__(self, "passed", passed)


@dataclass(frozen=True)
class WalkForwardReport:
    mode: WindowMode
    windows: List[Window]
    results: List[WindowResult]
    window_count: int = field(init=False)
    pass_count: int = field(init=False)
    fail_count: int = field(init=False)
    failed_windows: List[int] = field(init=False)
    strongest_window: Optional[int] = field(init=False)
    weakest_window: Optional[int] = field(init=False)
    overall_pass: bool = field(init=False)

    def __post_init__(self) -> None:
        window_count = len(self.results)
        passed = [r for r in self.results if r.passed]
        failed = [r for r in self.results if not r.passed]
        passed_count = len(passed)
        failed_count = len(failed)
        failed_windows = [r.window.index for r in failed]
        strongest = max(self.results, key=lambda r: r.out_of_sample_score, default=None)
        weakest = min(self.results, key=lambda r: r.out_of_sample_score, default=None)
        overall_pass = failed_count == 0 and bool(self.results)
        object.__setattr__(self, "window_count", window_count)
        object.__setattr__(self, "pass_count", passed_count)
        object.__setattr__(self, "fail_count", failed_count)
        object.__setattr__(self, "failed_windows", failed_windows)
        object.__setattr__(self, "strongest_window", strongest.window.index if strongest else None)
        object.__setattr__(self, "weakest_window", weakest.window.index if weakest else None)
        object.__setattr__(self, "overall_pass", overall_pass)


# --------------------
# Window construction
# --------------------


def build_windows(
    n: int,
    train_size: int,
    test_size: int,
    step_size: Optional[int] = None,
    mode: WindowMode = WindowMode.ROLLING,
) -> List[Window]:
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive.")
    out: List[Window] = []
    idx = 0
    if mode == WindowMode.ANCHORED:
        start = 0
        while True:
            train_end = start + train_size
            test_start = train_end
            test_end = test_start + test_size
            if test_end > n:
                break
            out.append(Window(index=idx, train_start=start, train_end=train_end, test_start=test_start, test_end=test_end))
            idx += 1
        return out
    if mode == WindowMode.EXPANDING:
        start = 0
        current_train = train_size
        while True:
            train_start = start
            train_end = current_train
            test_start = train_end
            test_end = test_start + test_size
            if test_end > n:
                break
            out.append(Window(index=idx, train_start=train_start, train_end=train_end, test_start=test_start, test_end=test_end))
            step = step_size or test_size
            current_train += step
            idx += 1
        return out
    # ROLLING default
    train_start = 0
    step = step_size or test_size
    while True:
        train_end = train_start + train_size
        test_start = train_end
        test_end = test_start + test_size
        if test_end > n:
            break
        out.append(Window(index=idx, train_start=train_start, train_end=train_end, test_start=test_start, test_end=test_end))
        train_start += step
        idx += 1
    return out


# --------------------
# Validation safeguards
# --------------------


def validate_no_future_leakage(windows: Sequence[Window], n: int) -> None:
    if not windows:
        raise ValueError("No windows provided.")
    last_test_end = 0
    for w in windows:
        if not (0 <= w.train_start <= w.train_end <= w.test_start <= w.test_end <= n):
            raise ValueError(f"Window {w.index} has invalid bounds: {w}")
        if w.test_start < w.train_end:
            raise ValueError(f"Window {w.index} leaks future: test overlaps train.")
        if w.test_start < last_test_end:
            raise ValueError(f"Window {w.index} starts before previous window ended.")
        last_test_end = w.test_end


# --------------------
# Scoring
# --------------------


def score_in_sample(scores: Sequence[float], window: Window) -> float:
    segment = scores[window.train_start:window.train_end]
    return float(sum(segment)) if segment else 0.0


def score_out_of_sample(scores: Sequence[float], window: Window) -> float:
    segment = scores[window.test_start:window.test_end]
    return float(sum(segment)) if segment else 0.0


def run_walk_forward(
    scores: Sequence[float],
    train_size: int,
    test_size: int,
    mode: WindowMode = WindowMode.ROLLING,
    step_size: Optional[int] = None,
) -> WalkForwardReport:
    if len(scores) < train_size + test_size:
        raise ValueError("Not enough data for the requested train/test sizes.")
    windows = build_windows(len(scores), train_size, test_size, step_size=step_size, mode=mode)
    validate_no_future_leakage(windows, len(scores))
    results: List[WindowResult] = []
    for w in windows:
        in_score = score_in_sample(scores, w)
        out_score = score_out_of_sample(scores, w)
        results.append(WindowResult(window=w, in_sample_score=in_score, out_of_sample_score=out_score))
    return WalkForwardReport(mode=mode, windows=windows, results=results)


# --------------------
# Reporting
# --------------------


def render_report(report: WalkForwardReport) -> str:
    mode = report.mode.value
    lines = [
        f"# Walk-Forward Report — {mode}",
        "",
        "## Summary",
        f"- Mode: {mode}",
        f"- Windows: {report.window_count}",
        f"- Passed: {report.pass_count}",
        f"- Failed: {report.fail_count}",
        f"- Overall: {'PASS' if report.overall_pass else 'FAIL'}",
        "",
        "## Windows",
        "| Window | Train | Test | In-sample | Out-of-sample | Degradation | Pass |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in report.results:
        w = r.window
        lines.append(
            f"| {w.index} | {w.train_start}-{w.train_end} | {w.test_start}-{w.test_end} | {r.in_sample_score:.4f} | {r.out_of_sample_score:.4f} | {r.degradation_ratio:.2f} | {'PASS' if r.passed else 'FAIL'} |"
        )
    lines += [
        "",
        "## Failed Windows",
        ", ".join(str(i) for i in report.failed_windows) or "None",
        "",
        "## Strongest Window",
        str(report.strongest_window),
        "",
        "## Weakest Window",
        str(report.weakest_window),
        "",
        "## Notes",
        "- Do not use out-of-sample results to re-select parameters.",
        "- Future leakage checks enforced by validate_no_future_leakage().",
    ]
    return "\n".join(lines)
