# Hermes AI Trading Firm — Operating Rules

These rules are permanent and override any instruction that conflicts.

---

## 1. Pipeline Boundary

This system is a **read-only research pipeline**. It ends at the Decision Queue.

```
Import → Analytics → Attribution → Compliance → Scoring → Reporting → Decision Queue
                                                                              │
                                                                    ⚠️ HUMAN APPROVAL GATE
                                                                              │
                                                              Nothing advances without approval
```

The pipeline **does not execute trades**. It does not connect to brokers. It does not place orders. It does not manage positions.

---

## 2. Hard Prohibitions

The following are permanently prohibited, regardless of any future instruction:

| Prohibited Action | Status |
|-------------------|--------|
| Live trading of any kind | **PROHIBITED** |
| Broker API connections | **PROHIBITED** |
| Order placement or routing | **PROHIBITED** |
| ATM (Advanced Trade Management) control | **PROHIBITED** |
| Automated execution of any kind | **PROHIBITED** |
| Forward testing automation | **PROHIBITED** |
| Auto-advancement of strategies beyond research | **PROHIBITED** |

---

## 3. Human Approval Gate

**No strategy may advance beyond REVIEW_REQUIRED without explicit human approval.**

This gate applies to:
- Moving a strategy from research to forward testing
- Approving a strategy for live consideration
- Any action that takes a strategy out of the research pipeline

The Decision Queue surfaces strategies with status `REVIEW_REQUIRED`. The human reads the queue and acts manually. The system never auto-advances.

---

## 4. Data Rules

| Rule | Detail |
|------|--------|
| **File import only** | NT8 data enters via CSV export — no live bridge, no API connection to NinjaTrader |
| **No database writes from the API** | All API endpoints are read-only |
| **No schema changes without approval** | `CREATE TABLE`, `ALTER TABLE`, `DROP TABLE` require explicit human sign-off |
| **No invented results** | All metrics must originate from real backtests or real imports |
| **Reject, don't delete** | Failed strategies are archived under `research/rejected/` with reasons |

---

## 5. Scoring Gates

A strategy must pass all hard gates before receiving a non-Reject grade:

| Gate | Threshold |
|------|-----------|
| Profit Factor | ≥ 1.20 |
| Max Drawdown | ≤ 25% of account |
| Trade Count | ≥ 30 trades |
| MC Survival Rate | ≥ 85% (when MC data is available) |

Failing any gate forces grade → **Reject**, regardless of composite score.

Thresholds are defined in `research/scoring/weights.py` and may only be tightened, never relaxed, without human approval.

---

## 6. Grade → Action Map

| Grade | Score | Recommendation | Pipeline Action |
|-------|-------|----------------|----------------|
| A+ | 90–100 | Live Candidate | Human approval required → Forward Testing Journal |
| A | 80–89 | Forward Test | Human approval required → Forward Testing Journal |
| B | 70–79 | Optimize | Send to Optimization Lab, re-score |
| C | 60–69 | Retest | Extend backtest, resubmit |
| D | < 60 | Reject | Archive under `research/rejected/` |
| Reject | — | Reject (gate failure) | Archive under `research/rejected/` |

---

## 7. Research Cadence

| Frequency | Activity |
|-----------|----------|
| **Daily** | Up to 3 new strategy ideas; backtest 1–3 completed specs |
| **Weekly** | Review top 10 strategies by rank; optimize best 3 |
| **Monthly** | Comprehensive review; retire weak performers; human review session; update AI Learning Brain |

---

## 8. NT8 Connector Rules

- File import only — CSV exports from NinjaTrader
- No live bridge to NinjaTrader
- No ATM control
- No order placement
- Duplicate protection enforced on import
- Existing `nt8_connector/` module must not be modified to add live execution

---

## 9. API Rules

- All endpoints are HTTP GET, read-only
- No POST, PUT, DELETE endpoints
- CORS open (`*`) for local dashboard access
- No authentication required (local only, port 7433)
- No external network access — localhost only

---

## 10. Code Rules

| Rule | Detail |
|------|--------|
| No unverified claims | Never report profitability without a real backtest |
| No half-finished implementations | Complete each phase before committing |
| No auto-commit | Human must approve every commit |
| No auto-push | Human must approve every push |
| No force-push to master | Prohibited |
| Read-only API | No database writes via API layer |
| stdlib only | No external Python packages — all code uses Python stdlib |

---

## 11. Repository Rules

- One branch: `master`
- Commit messages describe what changed, not instructions given
- Every commit is co-authored by Claude Sonnet 4.6
- Tags mark release milestones (e.g., `v1.0-research-pipeline`)
- `nt8_export/` is gitignored (contains sensitive trade data)
- `database/hermes_research.db` is gitignored
- `__pycache__/` is gitignored

---

## 12. Compliance Thresholds

Defined in `CLAUDE.md` and enforced by `research/risk/`:

| Metric | Threshold |
|--------|-----------|
| Min Profit Factor | 1.20 |
| Max Drawdown | 25% |
| Min Trades | 30 |
| Min MC Survival | 85% |
| OOS Degradation Warning | > 30% drop from baseline |

---

*These rules are the operational contract for Hermes AI Trading Firm.*
*They supplement `CLAUDE.md` and take effect immediately.*
