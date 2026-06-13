-- Hermes AI Trading Firm — Database Initialization
-- SQLite database: hermes_research.db

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

BEGIN TRANSACTION;

-- Markets
CREATE TABLE IF NOT EXISTS markets (
    market_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_class TEXT NOT NULL, -- stocks, futures, options, crypto
    symbol TEXT NOT NULL,
    name TEXT,
    exchange TEXT,
    currency TEXT,
    session_hours TEXT,
    tick_size REAL,
    pip_value REAL,
    margin_requirement REAL,
    current_regime TEXT, -- trending, sideways, volatile, mean-reverting
    regime_updated_at TEXT,
    liquidity_score INTEGER,
    notes TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_markets_asset_class ON markets(asset_class);
CREATE INDEX IF NOT EXISTS idx_markets_symbol ON markets(symbol);
CREATE INDEX IF NOT EXISTS idx_markets_regime ON markets(current_regime);
CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(is_active);

-- Strategy Ideas
CREATE TABLE IF NOT EXISTS strategy_ideas (
    idea_id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_name TEXT NOT NULL,
    market_id INTEGER,
    asset_class TEXT,
    symbol TEXT,
    timeframe TEXT,
    strategy_type TEXT, -- breakout, trend-following, mean-reversion, vwap, fvg, liquidity-sweep, session-based
    description TEXT,
    hypothesis TEXT,
    failure_conditions TEXT,
    suggested_filters TEXT,
    suggested_indicators TEXT,
    source TEXT DEFAULT 'strategy_factory',
    status TEXT DEFAULT 'pending', -- pending, spec_created, tested, rejected, approved
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_ideas_status ON strategy_ideas(status);
CREATE INDEX IF NOT EXISTS idx_ideas_type ON strategy_ideas(strategy_type);
CREATE INDEX IF NOT EXISTS idx_ideas_asset_class ON strategy_ideas(asset_class);
CREATE INDEX IF NOT EXISTS idx_ideas_market ON strategy_ideas(market_id);

-- Strategy Specifications
CREATE TABLE IF NOT EXISTS strategy_specs (
    spec_id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER,
    spec_name TEXT NOT NULL,
    market_id INTEGER,
    asset_class TEXT,
    symbol TEXT,
    timeframe TEXT,
    session TEXT,
    entry_rules TEXT NOT NULL,
    exit_rules TEXT NOT NULL,
    stop_loss_type TEXT,
    stop_loss_value REAL,
    profit_target_type TEXT,
    profit_target_value REAL,
    risk_rules TEXT,
    filters TEXT,
    optimization_variables TEXT,
    why_edge_exists TEXT,
    why_strategy_may_fail TEXT,
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft', -- draft, coding, backtesting, optimized, regime_analyzed, approved, rejected
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (idea_id) REFERENCES strategy_ideas(idea_id),
    FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_specs_status ON strategy_specs(status);
CREATE INDEX IF NOT EXISTS idx_specs_idea ON strategy_specs(idea_id);
CREATE INDEX IF NOT EXISTS idx_specs_symbol ON strategy_specs(symbol);
CREATE INDEX IF NOT EXISTS idx_specs_market ON strategy_specs(market_id);

-- Backtests
CREATE TABLE IF NOT EXISTS backtests (
    backtest_id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id INTEGER NOT NULL,
    backtest_name TEXT,
    data_source TEXT,
    data_start_date TEXT,
    data_end_date TEXT,
    commission_type TEXT, -- fixed, percent
    commission_value REAL,
    slippage_type TEXT, -- fixed, percent, tick
    slippage_value REAL,
    initial_capital REAL,
    net_profit REAL,
    gross_profit REAL,
    gross_loss REAL,
    profit_factor REAL,
    win_rate REAL,
    loss_rate REAL,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    average_win REAL,
    average_loss REAL,
    max_win REAL,
    max_loss REAL,
    max_drawdown REAL,
    max_drawdown_pct REAL,
    recovery_factor REAL,
    sharpe_ratio REAL,
    sortino_ratio REAL,
    expectancy REAL,
    expectancy_per_trade REAL,
    avg_trade_duration TEXT,
    max_consecutive_wins INTEGER,
    max_consecutive_losses INTEGER,
    profit_per_month REAL,
    equity_curve_json TEXT, -- JSON array of {date, equity} points
    trade_list_json TEXT,  -- JSON array of trade records
    is_in_sample INTEGER DEFAULT 1,
    notes TEXT,
    baseline_backtest_id INTEGER, -- self-reference for baseline vs optimized comparison
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id),
    FOREIGN KEY (baseline_backtest_id) REFERENCES backtests(backtest_id)
);
CREATE INDEX IF NOT EXISTS idx_backtests_spec ON backtests(spec_id);
CREATE INDEX IF NOT EXISTS idx_backtests_profit_factor ON backtests(profit_factor);
CREATE INDEX IF NOT EXISTS idx_backtests_sharpe ON backtests(sharpe_ratio);
CREATE INDEX IF NOT EXISTS idx_backtests_in_sample ON backtests(is_in_sample);
CREATE INDEX IF NOT EXISTS idx_backtests_created ON backtests(created_at);

-- Optimizations
CREATE TABLE IF NOT EXISTS optimizations (
    optimization_id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id INTEGER NOT NULL,
    backtest_id INTEGER, -- baseline backtest reference
    method TEXT, -- grid_search, random_search, bayesian, genetic
    parameter_grid_json TEXT NOT NULL, -- {param: [values]}
    best_parameters_json TEXT NOT NULL,
    best_backtest_result_id INTEGER, -- reference to backtests table
    baseline_profit_factor REAL,
    optimized_profit_factor REAL,
    baseline_expectancy REAL,
    optimized_expectancy REAL,
    baseline_max_drawdown REAL,
    optimized_max_drawdown REAL,
    stability_score REAL, -- measure of parameter sensitivity
    overfit_warning INTEGER DEFAULT 0,
    overfit_notes TEXT,
    walk_forward_required INTEGER DEFAULT 1,
    status TEXT DEFAULT 'running', -- running, completed, failed, rejected
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id),
    FOREIGN KEY (backtest_id) REFERENCES backtests(backtest_id),
    FOREIGN KEY (best_backtest_result_id) REFERENCES backtests(backtest_id)
);
CREATE INDEX IF NOT EXISTS idx_optimizations_spec ON optimizations(spec_id);
CREATE INDEX IF NOT EXISTS idx_optimizations_status ON optimizations(status);
CREATE INDEX IF NOT EXISTS idx_optimizations_overfit ON optimizations(overfit_warning);

-- Regime Analysis
CREATE TABLE IF NOT EXISTS regime_analysis (
    regime_analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id INTEGER NOT NULL,
    backtest_id INTEGER,
    market_id INTEGER,
    regime_model TEXT, -- markov, hmm, rule_based
    analysis_method TEXT, -- markov_transition_matrix, hmm_inferred, rule_based
    regimes_detected TEXT, -- JSON array of regime names
    bull_performance_json TEXT, -- {trades, profit_factor, drawdown} or null
    bear_performance_json TEXT,
    sideways_performance_json TEXT,
    transition_performance_json TEXT,
    best_regime TEXT,
    worst_regime TEXT,
    regime_filter_recommended INTEGER, -- 1 if should only trade in specific regime
    recommended_regimes TEXT, -- JSON array of recommended regime names
    transition_matrix_json TEXT, -- Markov transition probabilities
    hidden_states_json TEXT, -- HMM inferred states
    comparison_without_filter_profit_factor REAL,
    comparison_with_filter_profit_factor REAL,
    conclusion TEXT,
    status TEXT DEFAULT 'pending', -- pending, completed, failed
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id),
    FOREIGN KEY (backtest_id) REFERENCES backtests(backtest_id),
    FOREIGN KEY (market_id) REFERENCES markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_regime_spec ON regime_analysis(spec_id);
CREATE INDEX IF NOT EXISTS idx_regime_market ON regime_analysis(market_id);
CREATE INDEX IF NOT EXISTS idx_regime_model ON regime_analysis(regime_model);

-- Forward Tests
CREATE TABLE IF NOT EXISTS forward_tests (
    forward_test_id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id INTEGER NOT NULL,
    approved_strategy_id INTEGER, -- reference when moved from approved
    symbol TEXT,
    timeframe TEXT,
    start_date TEXT,
    end_date TEXT,
    status TEXT DEFAULT 'active', -- active, paused, completed, failed, passed
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    net_pnl REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    current_drawdown REAL DEFAULT 0,
    mistakes_count INTEGER DEFAULT 0,
    rule_violations_count INTEGER DEFAULT 0,
    notes TEXT,
    result_json TEXT, -- detailed results
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id),
    FOREIGN KEY (approved_strategy_id) REFERENCES approved_strategies(approved_strategy_id)
);
CREATE INDEX IF NOT EXISTS idx_forward_spec ON forward_tests(spec_id);
CREATE INDEX IF NOT EXISTS idx_forward_status ON forward_tests(status);
CREATE INDEX IF NOT EXISTS idx_forward_symbol ON forward_tests(symbol);

-- Approved Strategies
CREATE TABLE IF NOT EXISTS approved_strategies (
    approved_strategy_id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id INTEGER NOT NULL,
    strategy_name TEXT NOT NULL,
    asset_class TEXT,
    symbol TEXT,
    timeframe TEXT,
    session TEXT,
    approval_reason TEXT,
    approved_by TEXT DEFAULT 'human', -- human, committee
    approval_date TEXT,
    expected_annual_return REAL,
    expected_max_drawdown REAL,
    current_forward_test_id INTEGER,
    status TEXT DEFAULT 'active', -- active, forward_testing, retired, failed
    ai_brain_rating REAL, -- rating from AI Learning Brain
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id),
    FOREIGN KEY (current_forward_test_id) REFERENCES forward_tests(forward_test_id)
);
CREATE INDEX IF NOT EXISTS idx_approved_spec ON approved_strategies(spec_id);
CREATE INDEX IF NOT EXISTS idx_approved_status ON approved_strategies(status);
CREATE INDEX IF NOT EXISTS idx_approved_symbol ON approved_strategies(symbol);

-- Rejected Strategies
CREATE TABLE IF NOT EXISTS rejected_strategies (
    rejected_strategy_id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER,
    spec_id INTEGER,
    strategy_name TEXT NOT NULL,
    asset_class TEXT,
    symbol TEXT,
    rejection_stage TEXT NOT NULL, -- baseline, risk_review, regime, optimization, walk_forward, monte_carlo, human_approval
    rejection_reason TEXT NOT NULL,
    failed_metrics_json TEXT, -- JSON of the metrics that failed thresholds
    suggestion TEXT, -- how to improve or why to avoid similar approaches
    risk_level TEXT, -- high, medium, low
    archived_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (idea_id) REFERENCES strategy_ideas(idea_id),
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id)
);
CREATE INDEX IF NOT EXISTS idx_rejected_stage ON rejected_strategies(rejection_stage);
CREATE INDEX IF NOT EXISTS idx_rejected_spec ON rejected_strategies(spec_id);
CREATE INDEX IF NOT EXISTS idx_rejected_archived ON rejected_strategies(archived_at);

-- Research Notes
CREATE TABLE IF NOT EXISTS research_notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id INTEGER,
    idea_id INTEGER,
    note_type TEXT, -- observation, pattern, lesson, idea, improvement
    content TEXT NOT NULL,
    tags TEXT, -- JSON array of tags
    related_strategies_json TEXT, -- JSON array of related spec/idea IDs
    confidence INTEGER, -- 0-100
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (spec_id) REFERENCES strategy_specs(spec_id),
    FOREIGN KEY (idea_id) REFERENCES strategy_ideas(idea_id)
);
CREATE INDEX IF NOT EXISTS idx_notes_spec ON research_notes(spec_id);
CREATE INDEX IF NOT EXISTS idx_notes_type ON research_notes(note_type);
CREATE INDEX IF NOT EXISTS idx_notes_tags ON research_notes(tags);

COMMIT TRANSACTION;

-- Insert default asset class values for reference
INSERT OR IGNORE INTO markets (asset_class, symbol, name, is_active) VALUES 
    ('stocks', 'SPY', 'SPDR S&P 500 ETF', 1),
    ('stocks', 'AAPL', 'Apple Inc.', 1),
    ('futures', 'ES', 'E-mini S&P 500', 1),
    ('futures', 'NQ', 'E-mini Nasdaq-100', 1),
    ('futures', 'MNQ', 'Micro E-mini Nasdaq-100', 1),
    ('futures', 'CL', 'Crude Oil WTI', 1),
    ('futures', 'GC', 'Gold Futures', 1),
    ('options', 'SPX', 'S&P 500 Index Options', 1),
    ('options', 'QQQ', 'Invesco QQQ Options', 1),
    ('crypto', 'BTCUSDT', 'Bitcoin/Tether', 1),
    ('crypto', 'ETHUSDT', 'Ethereum/Tether', 1);

-- Insert a research note about database initialization
INSERT INTO research_notes (note_type, content, tags, confidence) VALUES 
    ('observation', 'Database initialized with core schema for Hermes AI Trading Firm.', 
     '["database","initialization","setup"]', 100);
