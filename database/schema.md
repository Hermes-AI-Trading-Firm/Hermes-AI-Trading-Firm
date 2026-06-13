# Database Schema Documentation

Generated from `database/init.sql`.

## Tables

### markets
Canonical registry of tradeable instruments.

| Column | Type | Notes |
|--------|------|-------|
| market_id | PK AUTO | |
| asset_class | TEXT | stocks, futures, options, crypto |
| symbol | TEXT | Instrument symbol |
| name | TEXT | Display name |
| exchange | TEXT | |
| currency | TEXT | |
| session_hours | TEXT | |
| tick_size | REAL | |
| pip_value | REAL | |
| margin_requirement | REAL | |
| current_regime | TEXT | trending, sideways, volatile, mean-reverting |
| regime_updated_at | TEXT | |
| liquidity_score | INTEGER | |
| notes | TEXT | |
| is_active | INTEGER DEFAULT 1 | |
| created_at, updated_at | TEXT | UTC timestamp |

### strategy_ideas
Raw ideas from Strategy Factory before full spec.

| Column | Notes |
|--------|-------|
| idea_id | PK |
| idea_name | |
| market_id | FK markets |
| asset_class | |
| symbol | |
| timeframe | |
| strategy_type | breakout, trend-following, vwap, etc. |
| description / hypothesis / failure_conditions / suggested_filters | TEXT |
| source | DEFAULT strategy_factory |
| status | pending, spec_created, tested, rejected, approved |
| created_at, updated_at | |

### strategy_specs
Complete specifications ready for coding / backtesting.

| Column | Notes |
|--------|-------|
| spec_id | PK |
| idea_id | FK strategy_ideas |
| spec_name | |
| market_id | FK markets |
| asset_class | |
| symbol | |
| timeframe | |
| session | |
| entry_rules, exit_rules, risk_rules, filters | TEXT |
| stop_loss_type, profit_target_type | TEXT |
| stop_loss_value, profit_target_value | REAL |
| optimization_variables | JSON |
| why_edge_exists / why_strategy_may_fail | TEXT |
| version | DEFAULT 1 |
| status | draft, coding, backtesting, optimized, regime_analyzed, approved, rejected |
| created_at, updated_at | |

### backtests
Backtest results from Backtesting Lab.

| Column | Notes |
|--------|-------|
| backtest_id | PK |
| spec_id | FK strategy_specs |
| backtest_name | |
| data_source / data_start_date / data_end_date | |
| commission_type / commission_value / slippage_type / slippage_value | |
| initial_capital / net_profit / gross_profit / gross_loss | |
| profit_factor / win_rate / loss_rate | |
| total_trades / winning_trades / losing_trades | |
| average_win / average_loss / max_win / max_loss | |
| max_drawdown / max_drawdown_pct | |
| recovery_factor / sharpe_ratio / sortino_ratio / expectancy | |
| expectancy_per_trade / avg_trade_duration | |
| max_consecutive_wins / max_consecutive_losses | |
| profit_per_month | |
| equity_curve_json / trade_list_json | JSON |
| is_in_sample | DEFAULT 1 |
| notes | |
| baseline_backtest_id | FK backtests |
| created_at | |

### optimizations
Optimization runs from Optimization Lab.

| Column | Notes |
|--------|-------|
| optimization_id | PK |
| spec_id | FK strategy_specs |
| backtest_id | Baseline reference |
| method | grid_search, random_search, bayesian, genetic |
| parameter_grid_json | JSON |
| best_parameters_json | JSON |
| best_backtest_result_id | FK backtests |
| baseline_profit_factor / optimized_profit_factor | |
| baseline_expectancy / optimized_expectancy | |
| baseline_max_drawdown / optimized_max_drawdown | |
| stability_score | |
| overfit_warning | DEFAULT 0 |
| overfit_notes | |
| walk_forward_required | DEFAULT 1 |
| status | running, completed, failed, rejected |
| created_at / completed_at | |

### regime_analysis
Regime testing results from Market Regime Lab.

| Column | Notes |
|--------|-------|
| regime_analysis_id | PK |
| spec_id | FK strategy_specs |
| backtest_id | FK backtests |
| market_id | FK markets |
| regime_model | markov, hmm, rule_based |
| analysis_method | markov_transition_matrix, hmm_inferred, rule_based |
| regimes_detected | JSON array |
| bull/bear/sideways/transition_performance_json | JSON |
| best_regime / worst_regime | |
| regime_filter_recommended | 0/1 |
| recommended_regimes | JSON array |
| transition_matrix_json | Markov probabilities |
| hidden_states_json | HMM inferred states |
| comparison_without_filter_profit_factor | |
| comparison_with_filter_profit_factor | |
| conclusion | |
| status / created_at | |

### forward_tests
Paper-trading records from Forward Testing Journal.

| Column | Notes |
|--------|-------|
| forward_test_id | PK |
| spec_id | FK strategy_specs |
| approved_strategy_id | FK approved_strategies |
| symbol / timeframe | |
| start_date / end_date | |
| status | active, paused, completed, failed, passed |
| total_trades / winning_trades / losing_trades | |
| net_pnl / max_drawdown / current_drawdown | |
| mistakes_count / rule_violations_count | |
| notes / result_json | JSON |
| created_at / updated_at / completed_at | |

### approved_strategies
Strategies that passed all gates and were approved.

| Column | Notes |
|--------|-------|
| approved_strategy_id | PK |
| spec_id | FK strategy_specs |
| strategy_name | |
| asset_class / symbol / timeframe / session | |
| approval_reason | |
| approved_by | DEFAULT human |
| approval_date | |
| expected_annual_return / expected_max_drawdown | |
| current_forward_test_id | FK forward_tests |
| status | active, forward_testing, retired, failed |
| ai_brain_rating | |
| created_at / updated_at | |

### rejected_strategies
Archive of failed strategies.

| Column | Notes |
|--------|-------|
| rejected_strategy_id | PK |
| idea_id / spec_id | FK |
| strategy_name | |
| asset_class / symbol | |
| rejection_stage | baseline, risk_review, regime, optimization, walk_forward, monte_carlo, human_approval |
| rejection_reason | |
| failed_metrics_json | JSON |
| suggestion | |
| risk_level | high, medium, low |
| archived_at | |

### research_notes
AI Learning Brain notes.

| Column | Notes |
|--------|-------|
| note_id | PK |
| spec_id / idea_id | FK |
| note_type | observation, pattern, lesson, idea, improvement |
| content | |
| tags | JSON |
| related_strategies_json | JSON array |
| confidence | 0-100 |
| created_at / updated_at | |
