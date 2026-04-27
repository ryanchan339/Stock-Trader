from __future__ import annotations

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "return_1d",
    "return_5d",
    "return_10d",
    "return_20d",
    "volatility_10d",
    "volatility_20d",
    "volume_z_20d",
    "ma_ratio_10d",
    "ma_ratio_20d",
    "ma_ratio_50d",
    "rsi_14d",
    "relative_return_5d",
    "relative_return_20d",
    "spy_return_5d",
    "spy_return_20d",
    "spy_volatility_20d",
    "rank_return_5d",
    "rank_return_20d",
    "rank_relative_return_20d",
    "rank_low_volatility_20d",
    "rank_ma_ratio_20d",
    "rank_rsi_14d",
]


def load_price_data(path: str) -> pd.DataFrame:
    data = pd.read_csv(path, parse_dates=["date"])
    expected = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = expected.difference(data.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    data = data.sort_values(["symbol", "date"]).reset_index(drop=True)
    data["symbol"] = data["symbol"].astype(str).str.upper()
    return data


def add_technical_features(data: pd.DataFrame, benchmark: str = "SPY") -> pd.DataFrame:
    frames = []
    for symbol, group in data.groupby("symbol", sort=False):
        group = group.sort_values("date").copy()
        close = group["close"]
        volume = group["volume"]

        group["return_1d"] = close.pct_change()
        group["return_5d"] = close.pct_change(5)
        group["return_10d"] = close.pct_change(10)
        group["return_20d"] = close.pct_change(20)
        group["volatility_10d"] = group["return_1d"].rolling(10).std()
        group["volatility_20d"] = group["return_1d"].rolling(20).std()
        group["volume_z_20d"] = (
            (volume - volume.rolling(20).mean()) / volume.rolling(20).std()
        )
        group["ma_ratio_10d"] = close / close.rolling(10).mean() - 1
        group["ma_ratio_20d"] = close / close.rolling(20).mean() - 1
        group["ma_ratio_50d"] = close / close.rolling(50).mean() - 1
        group["rsi_14d"] = _rsi(close, window=14)
        frames.append(group)

    featured = pd.concat(frames, ignore_index=True)
    benchmark_features = (
        featured.loc[featured["symbol"] == benchmark, ["date", "return_5d", "return_20d", "volatility_20d"]]
        .rename(
            columns={
                "return_5d": "spy_return_5d",
                "return_20d": "spy_return_20d",
                "volatility_20d": "spy_volatility_20d",
            }
        )
    )
    featured = featured.merge(benchmark_features, on="date", how="left")
    featured["relative_return_5d"] = featured["return_5d"] - featured["spy_return_5d"]
    featured["relative_return_20d"] = featured["return_20d"] - featured["spy_return_20d"]
    featured["rank_return_5d"] = featured.groupby("date")["return_5d"].rank(pct=True)
    featured["rank_return_20d"] = featured.groupby("date")["return_20d"].rank(pct=True)
    featured["rank_relative_return_20d"] = featured.groupby("date")["relative_return_20d"].rank(pct=True)
    featured["rank_low_volatility_20d"] = featured.groupby("date")["volatility_20d"].rank(
        pct=True,
        ascending=False,
    )
    featured["rank_ma_ratio_20d"] = featured.groupby("date")["ma_ratio_20d"].rank(pct=True)
    featured["rank_rsi_14d"] = featured.groupby("date")["rsi_14d"].rank(pct=True)
    return featured


def add_labels(data: pd.DataFrame, horizon: int = 5, benchmark: str = "SPY") -> pd.DataFrame:
    labelled = data.sort_values(["symbol", "date"]).copy()
    labelled["future_return"] = labelled.groupby("symbol")["close"].shift(-horizon) / labelled["close"] - 1

    benchmark_returns = (
        labelled.loc[labelled["symbol"] == benchmark, ["date", "future_return"]]
        .rename(columns={"future_return": "benchmark_future_return"})
    )
    labelled = labelled.merge(benchmark_returns, on="date", how="left")
    labelled["target_outperform_spy"] = (
        labelled["future_return"] > labelled["benchmark_future_return"]
    ).astype(int)
    return labelled


def make_model_frame(
    data: pd.DataFrame,
    horizon: int = 5,
    benchmark: str = "SPY",
    include_benchmark: bool = False,
) -> pd.DataFrame:
    featured = add_technical_features(data, benchmark=benchmark)
    labelled = add_labels(featured, horizon=horizon, benchmark=benchmark)
    if not include_benchmark:
        labelled = labelled[labelled["symbol"] != benchmark]

    columns = ["symbol", "date", "close", "future_return", "benchmark_future_return", "target_outperform_spy"]
    frame = labelled[columns + FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    return frame.dropna().sort_values(["date", "symbol"]).reset_index(drop=True)


def _rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window).mean()
    avg_loss = loss.rolling(window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))
