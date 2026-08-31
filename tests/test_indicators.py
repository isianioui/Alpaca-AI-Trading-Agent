import numpy as np
import pandas as pd
import pytest

from src.indicators import build_feature_snapshot, ema, macd, rsi, sma


def make_synthetic_bars(n=100, start=100.0, drift=0.2, seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(loc=drift / 100, scale=0.01, size=n)
    close = start * np.cumprod(1 + returns)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": rng.integers(1_000_000, 5_000_000, size=n),
    }, index=idx)


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5])
    result = sma(s, window=2)
    assert result.iloc[-1] == pytest.approx(4.5)


def test_ema_shorter_lag_than_sma():
    bars = make_synthetic_bars()
    sma_20 = sma(bars["close"], 20)
    ema_20 = ema(bars["close"], 20)
    # EMA should differ from SMA (it weights recent prices more) but stay close in magnitude
    assert ema_20.iloc[-1] != sma_20.iloc[-1]
    assert abs(ema_20.iloc[-1] - sma_20.iloc[-1]) < bars["close"].iloc[-1] * 0.2


def test_rsi_bounds():
    bars = make_synthetic_bars()
    r = rsi(bars["close"])
    valid = r.dropna()
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_rsi_strong_uptrend_is_high():
    n = 60
    close = pd.Series(np.linspace(100, 200, n))  # monotonic uptrend
    r = rsi(close)
    assert r.iloc[-1] > 70  # should read as overbought


def test_macd_has_expected_columns():
    bars = make_synthetic_bars()
    result = macd(bars["close"])
    assert set(result.columns) == {"macd", "signal", "histogram"}
    assert len(result) == len(bars)


def test_build_feature_snapshot_insufficient_history():
    tiny = make_synthetic_bars(n=5)
    snapshot = build_feature_snapshot(tiny)
    assert snapshot == {"error": "insufficient_history"}


def test_build_feature_snapshot_happy_path():
    bars = make_synthetic_bars(n=100)
    snapshot = build_feature_snapshot(bars)
    assert "error" not in snapshot
    for key in ["last_close", "day_change_pct", "sma_20", "rsi_14", "52w_high", "52w_low"]:
        assert key in snapshot
    assert snapshot["52w_low"] <= snapshot["last_close"] <= snapshot["52w_high"]
