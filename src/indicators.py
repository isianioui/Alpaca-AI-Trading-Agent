"""
Lightweight technical indicators computed with pandas/numpy only
(no TA-Lib dependency, keeps setup painless for judges).

These aren't meant to be a trading strategy on their own — they're
structured, numeric context that gets handed to the LLM so its
reasoning is grounded in real data instead of vibes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=window).mean()
    avg_loss = loss.rolling(window=window).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # Zero downside over the window means maximally overbought (RSI=100),
    # not undefined -- avoid propagating NaN in a pure uptrend.
    result = result.where(avg_loss != 0, 100.0)
    # Both gain and loss are zero (flat price): RSI is conventionally 50.
    result = result.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return result


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def volatility(series: pd.Series, window: int = 14) -> pd.Series:
    return series.pct_change().rolling(window=window).std() * np.sqrt(252)


def build_feature_snapshot(bars: pd.DataFrame) -> dict:
    """
    Reduce a full OHLCV history down to the latest values of every
    indicator, in a compact dict that's cheap to drop into an LLM prompt.
    """
    if bars.empty or len(bars) < 20:
        return {"error": "insufficient_history"}

    close = bars["close"]

    sma_20 = sma(close, 20)
    sma_50 = sma(close, 50) if len(close) >= 50 else pd.Series([np.nan] * len(close))
    rsi_14 = rsi(close, 14)
    macd_df = macd(close)
    vol_14 = volatility(close, 14)

    latest_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])

    return {
        "last_close": round(latest_close, 2),
        "day_change_pct": round((latest_close - prev_close) / prev_close * 100, 3),
        "sma_20": round(float(sma_20.iloc[-1]), 2) if not pd.isna(sma_20.iloc[-1]) else None,
        "sma_50": round(float(sma_50.iloc[-1]), 2) if not pd.isna(sma_50.iloc[-1]) else None,
        "price_vs_sma20_pct": round((latest_close - sma_20.iloc[-1]) / sma_20.iloc[-1] * 100, 2)
        if not pd.isna(sma_20.iloc[-1]) else None,
        "rsi_14": round(float(rsi_14.iloc[-1]), 2) if not pd.isna(rsi_14.iloc[-1]) else None,
        "macd_histogram": round(float(macd_df["histogram"].iloc[-1]), 4)
        if not pd.isna(macd_df["histogram"].iloc[-1]) else None,
        "annualized_volatility_pct": round(float(vol_14.iloc[-1]) * 100, 2)
        if not pd.isna(vol_14.iloc[-1]) else None,
        "52w_high": round(float(close.max()), 2),
        "52w_low": round(float(close.min()), 2),
    }
