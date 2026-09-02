from src.exit_engine import ExitEngine, ExitLimits


def make_engine(**overrides) -> ExitEngine:
    limits = ExitLimits(
        stop_loss_pct=overrides.get("stop_loss_pct", -0.08),
        take_profit_pct=overrides.get("take_profit_pct", 0.15),
        options_profit_target_pct=overrides.get("options_profit_target_pct", 0.50),
        options_stop_multiple=overrides.get("options_stop_multiple", 2.0),
    )
    return ExitEngine(limits)


def stock_position(symbol="AAPL", unrealized_plpc=0.0) -> dict:
    return {
        "symbol": symbol,
        "qty": 10.0,
        "avg_entry_price": 100.0,
        "current_price": 100.0 * (1 + unrealized_plpc),
        "market_value": 1000.0,
        "unrealized_pl": 1000.0 * unrealized_plpc,
        "unrealized_plpc": unrealized_plpc,
    }


def option_position(symbol="AAPL260116P00200000", underlying="AAPL", unrealized_plpc=0.0) -> dict:
    return {
        "symbol": symbol,
        "underlying_symbol": underlying,
        "option_type": "put",
        "strike": 200.0,
        "expiration": "2026-01-16",
        "qty": -1.0,
        "avg_entry_price": 5.0,
        "current_price": 5.0 * (1 - unrealized_plpc),
        "market_value": -500.0,
        "unrealized_pl": 500.0 * unrealized_plpc,
        "unrealized_plpc": unrealized_plpc,
    }


# ---------------------------------------------------------------------- #
# Stock positions
# ---------------------------------------------------------------------- #
def test_stock_within_thresholds_holds():
    engine = make_engine()
    result = engine.evaluate_stock_position(stock_position(unrealized_plpc=0.03))
    assert result.action == "HOLD"
    assert result.asset_class == "stock"


def test_stock_past_stop_loss_closes_with_reason():
    engine = make_engine()
    result = engine.evaluate_stock_position(stock_position(unrealized_plpc=-0.12))
    assert result.action == "CLOSE"
    assert "STOP_LOSS" in result.reason


def test_stock_past_take_profit_closes_with_reason():
    engine = make_engine()
    result = engine.evaluate_stock_position(stock_position(unrealized_plpc=0.20))
    assert result.action == "CLOSE"
    assert "TAKE_PROFIT" in result.reason


def test_stock_stop_loss_boundary_triggers_close():
    engine = make_engine(stop_loss_pct=-0.08)
    result = engine.evaluate_stock_position(stock_position(unrealized_plpc=-0.08))
    assert result.action == "CLOSE"
    assert "STOP_LOSS" in result.reason


def test_stock_take_profit_boundary_triggers_close():
    engine = make_engine(take_profit_pct=0.15)
    result = engine.evaluate_stock_position(stock_position(unrealized_plpc=0.15))
    assert result.action == "CLOSE"
    assert "TAKE_PROFIT" in result.reason


def test_stock_just_inside_stop_loss_boundary_holds():
    engine = make_engine(stop_loss_pct=-0.08)
    result = engine.evaluate_stock_position(stock_position(unrealized_plpc=-0.0799))
    assert result.action == "HOLD"


def test_stock_just_inside_take_profit_boundary_holds():
    engine = make_engine(take_profit_pct=0.15)
    result = engine.evaluate_stock_position(stock_position(unrealized_plpc=0.1499))
    assert result.action == "HOLD"


# ---------------------------------------------------------------------- #
# Option positions (all positions this app opens are short premium)
# ---------------------------------------------------------------------- #
def test_option_within_thresholds_holds():
    engine = make_engine()
    result = engine.evaluate_option_position(option_position(unrealized_plpc=0.20))
    assert result.action == "HOLD"
    assert result.asset_class == "option"
    assert result.symbol == "AAPL"  # reports the underlying, not the OCC contract


def test_option_profit_target_closes_with_reason():
    engine = make_engine(options_profit_target_pct=0.50)
    result = engine.evaluate_option_position(option_position(unrealized_plpc=0.60))
    assert result.action == "CLOSE"
    assert "OPTIONS_PROFIT_TARGET" in result.reason


def test_option_stop_multiple_closes_with_reason():
    # options_stop_multiple=2.0 -> stop threshold is -100% (cost to close = 2x credit)
    engine = make_engine(options_stop_multiple=2.0)
    result = engine.evaluate_option_position(option_position(unrealized_plpc=-1.20))
    assert result.action == "CLOSE"
    assert "OPTIONS_STOP" in result.reason


def test_option_profit_target_boundary_triggers_close():
    engine = make_engine(options_profit_target_pct=0.50)
    result = engine.evaluate_option_position(option_position(unrealized_plpc=0.50))
    assert result.action == "CLOSE"
    assert "OPTIONS_PROFIT_TARGET" in result.reason


def test_option_stop_multiple_boundary_triggers_close():
    engine = make_engine(options_stop_multiple=2.0)
    result = engine.evaluate_option_position(option_position(unrealized_plpc=-1.0))
    assert result.action == "CLOSE"
    assert "OPTIONS_STOP" in result.reason


def test_option_just_inside_stop_boundary_holds():
    engine = make_engine(options_stop_multiple=2.0)
    result = engine.evaluate_option_position(option_position(unrealized_plpc=-0.99))
    assert result.action == "HOLD"


# ---------------------------------------------------------------------- #
# check_positions aggregates both asset classes
# ---------------------------------------------------------------------- #
def test_check_positions_evaluates_both_asset_classes():
    engine = make_engine()
    decisions = engine.check_positions(
        stock_positions=[stock_position(unrealized_plpc=-0.12)],
        option_positions=[option_position(unrealized_plpc=0.60)],
    )
    assert len(decisions) == 2
    assert {d.asset_class for d in decisions} == {"stock", "option"}
    assert all(d.action == "CLOSE" for d in decisions)


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("STOP_LOSS_PCT", raising=False)
    monkeypatch.delenv("TAKE_PROFIT_PCT", raising=False)
    monkeypatch.delenv("OPTIONS_PROFIT_TARGET_PCT", raising=False)
    monkeypatch.delenv("OPTIONS_STOP_MULTIPLE", raising=False)
    limits = ExitLimits.from_env()
    assert limits.stop_loss_pct == -0.08
    assert limits.take_profit_pct == 0.15
    assert limits.options_profit_target_pct == 0.50
    assert limits.options_stop_multiple == 2.0
