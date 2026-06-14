# Strategy Spec Field Mapping

Maps spec file fields to database columns in `strategy_specs` and `strategy_ideas`.

---

## Required Fields

| Spec file field | DB table | DB column | Notes |
|-----------------|----------|-----------|-------|
| `spec_name` | `strategy_specs` | `spec_name` | Dedup key — must be unique |
| `instrument` | `strategy_specs` | `asset_class`, `symbol` | Classified automatically (see below) |
| `timeframe` | `strategy_specs` | `timeframe` | Free text (e.g. "1 min", "5 min", "Daily") |
| `strategy_type` | `strategy_ideas` | `strategy_type` | Stored on the linked idea row |
| `description` | `strategy_specs` | `why_edge_exists` | Also stored on the idea row |
| `entry_rules` | `strategy_specs` | `entry_rules` | Required — cannot be empty |
| `exit_rules` | `strategy_specs` | `exit_rules` | Required — cannot be empty |
| `risk_rules` | `strategy_specs` | `risk_rules` | Required |
| `status` | `strategy_specs` | `status` | See status rules below |

---

## Optional Fields

| Spec file field | DB table | DB column | Notes |
|-----------------|----------|-----------|-------|
| `symbol` | `strategy_specs` | `symbol` | Overrides instrument-derived symbol |
| `session` | `strategy_specs` | `session` | e.g. "US Regular (09:30–16:00 ET)" |
| `stop_loss_type` | `strategy_specs` | `stop_loss_type` | fixed, percent, atr, range |
| `stop_loss_value` | `strategy_specs` | `stop_loss_value` | Numeric |
| `profit_target_type` | `strategy_specs` | `profit_target_type` | fixed, percent, range |
| `profit_target_value` | `strategy_specs` | `profit_target_value` | Numeric |
| `filters` | `strategy_specs` | `filters` | Free text |
| `optimization_variables` | `strategy_specs` | `optimization_variables` | Free text |
| `why_edge_exists` | `strategy_specs` | `why_edge_exists` | Overrides `description` if both present |
| `why_strategy_may_fail` | `strategy_specs` | `why_strategy_may_fail` | Free text |

---

## Instrument Classification

The `instrument` field is parsed to derive `asset_class` and `symbol`.

| Instrument | asset_class | symbol |
|------------|-------------|--------|
| `ES` | `futures` | `ES` |
| `NQ`, `MNQ`, `MES` | `futures` | root symbol |
| `ESZ26` | `futures` | `ES` (contract month stripped) |
| `NQ 09-26` | `futures` | `NQ` (space-split, first token) |
| `BTCUSDT` | `crypto` | `BTCUSDT` |
| `ETHUSDT` | `crypto` | `ETHUSDT` |
| `SPY`, `AAPL` | `stocks` | symbol |

Explicit `symbol` field overrides the derived value.

Known futures roots: ES, NQ, MNQ, MES, RTY, MYM, YM, M2K, CL, QM, GC, MGC, SI, ZB, ZN, ZF, ZT, NG, 6E, 6J, 6B, 6A, 6C, VX.

---

## Status Rules

| Spec value | Stored as | Notes |
|------------|-----------|-------|
| *(not set)* | `draft` | Default |
| `draft` | `draft` | ✓ |
| `spec_created` | `spec_created` | ✓ |
| `coding` | `coding` | ✓ |
| `backtesting` | `backtesting` | ✓ |
| `optimized` | `optimized` | ✓ |
| `regime_analyzed` | `regime_analyzed` | ✓ |
| `idea` | `draft` | Alias — normalised with warning |
| `researching` | `draft` | Alias — normalised with warning |
| `pending` | `draft` | Alias — normalised with warning |
| `approved` | `draft` | **BLOCKED** — human approval gate |
| `rejected` | `draft` | **BLOCKED** — human approval gate |

`approved` and `rejected` can only be set through the human approval workflow. The importer will override these with `draft` and emit a warning.

---

## strategy_ideas Linkage

For each imported spec, `spec_importer.py` finds or creates a row in `strategy_ideas`:

| Idea field | Value |
|------------|-------|
| `idea_name` | Same as `spec_name` |
| `strategy_type` | From spec `strategy_type` |
| `description` | From spec `description` |
| `asset_class`, `symbol`, `timeframe` | From spec |
| `source` | `spec_import` |
| `status` | `spec_created` |

The `spec_id` is then linked to this `idea_id` via `strategy_specs.idea_id`.

---

## Deduplication

Specs are deduplicated by `spec_name`. If a spec with the same name already exists:
- Without `--update-existing`: the import is **skipped**.
- With `--update-existing`: the existing row is **updated** in place (`spec_id` is preserved).

`spec_id` is never reassigned on update. `created_at` is preserved; `updated_at` is refreshed.
