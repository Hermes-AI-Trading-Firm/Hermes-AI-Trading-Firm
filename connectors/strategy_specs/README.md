# connectors/strategy_specs — Strategy Spec Importer

Imports strategy specification files (YAML, JSON, Markdown frontmatter) into
the `strategy_specs` table, creating the linked `strategy_ideas` row automatically.

No live trading. No broker connection. No order placement. Local file import only.  
Human approval is required before any strategy advances beyond the research pipeline.

---

## Files

| File | Purpose |
|------|---------|
| `spec_importer.py` | Main importer CLI |
| `sample_strategy_spec.yaml` | Sample YAML spec (ES VWAP Reversion) |
| `sample_strategy_spec.json` | Sample JSON spec (NQ Opening Range Breakout) |
| `mapping.md` | Field mapping: spec file → DB columns |

---

## Supported Formats

| Extension | Format | Requires |
|-----------|--------|---------|
| `.json` | JSON object | Python stdlib |
| `.yaml`, `.yml` | YAML mapping | PyYAML (installed) |
| `.md` | YAML frontmatter between `---` delimiters | PyYAML |

---

## Required Fields

Every spec file must contain:

```
spec_name        — unique name (dedup key)
instrument       — e.g. ES, NQ, MNQ, BTCUSDT, SPY
timeframe        — e.g. "1 min", "5 min", "Daily"
strategy_type    — breakout | trend-following | mean-reversion | vwap | fvg | liquidity-sweep | session-based
description      — edge hypothesis
entry_rules      — precise entry conditions
exit_rules       — stop, target, time exit
risk_rules       — position size, daily loss limit, session rules
```

---

## Usage

### Dry-run (validate without writing)
```powershell
python connectors/strategy_specs/spec_importer.py `
    --file connectors/strategy_specs/sample_strategy_spec.json `
    --dry-run
```

### Import new spec
```powershell
python connectors/strategy_specs/spec_importer.py `
    --file connectors/strategy_specs/sample_strategy_spec.yaml
```

### Import and update if spec_name already exists
```powershell
python connectors/strategy_specs/spec_importer.py `
    --file path/to/my_strategy.yaml `
    --update-existing
```

### Custom database path
```powershell
python connectors/strategy_specs/spec_importer.py `
    --file my_strategy.json `
    --db database/hermes_research.db
```

---

## Status Rules

`approved` and `rejected` cannot be set via import — the importer replaces
these with `draft` and emits a warning. The human approval gate is mandatory.

Safe status values: `draft`, `spec_created`, `coding`, `backtesting`, `optimized`, `regime_analyzed`

Accepted aliases (normalised to `draft`): `idea`, `researching`, `pending`

---

## After Import

Once a spec is imported, run the scoring pipeline to generate a score:

```powershell
# Score the new spec (replace N with its spec_id)
python research/scoring/score_from_backtests.py --spec-id N

# Or score all specs with backtest data
python research/scoring/score_from_backtests.py --all
```

Backtest data must be imported first via:
```powershell
python connectors/ninjatrader/backtest_ingestor.py `
    --summary path/to/performance_summary.csv `
    --trade-list path/to/trade_list.csv `
    --spec-id N
```

---

## Placing Your Spec Files

Strategy spec files can live anywhere. Recommended location for your own specs:

```
research/specs/
  ES_VWAP_REVERSION_v001.yaml
  NQ_ORB_v002.json
  ...
```

This directory is not gitignored — spec files are source-controlled research artifacts.
