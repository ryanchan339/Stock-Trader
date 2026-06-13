"""Intraday helpers for the live day-trading service.

This module holds the reusable, importable pieces of the intraday stack so the
CLI scripts in ``scripts/`` stay thin:

- Alpaca client construction from environment variables.
- Minute-bar fetching (historical REST) and normalisation.
- A baseline intraday signal (momentum + VWAP distance) that produces target
  weights. This is a placeholder to be replaced by a trained intraday model;
  it is deliberately simple and is NOT a validated edge.
- Out-of-band alerting via a Discord/Slack/Telegram-style webhook.

Nothing here submits orders. Order construction and risk controls live in
``scripts/alpaca_paper_trade.py`` and are reused by ``scripts/live_trader.py``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

MARKET_TZ = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- #
# Alpaca clients
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AlpacaCredentials:
    api_key: str
    secret_key: str
    paper: bool

    @property
    def has_keys(self) -> bool:
        return bool(
            self.api_key
            and self.secret_key
            and "replace_me" not in {self.api_key, self.secret_key}
        )


def credentials_from_env(paper_default: bool = True) -> AlpacaCredentials:
    from stock_trader.env import get_bool_env

    return AlpacaCredentials(
        api_key=os.environ.get("ALPACA_API_KEY", ""),
        secret_key=os.environ.get("ALPACA_SECRET_KEY", ""),
        paper=get_bool_env("ALPACA_PAPER", default=paper_default),
    )


def build_trading_client(creds: AlpacaCredentials):
    try:
        from alpaca.trading.client import TradingClient
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SystemExit(
            "alpaca-py is not installed. Run: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc
    return TradingClient(creds.api_key, creds.secret_key, paper=creds.paper)


def build_data_client(creds: AlpacaCredentials):
    try:
        from alpaca.data.historical import StockHistoricalDataClient
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SystemExit(
            "alpaca-py is not installed. Run: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc
    return StockHistoricalDataClient(creds.api_key, creds.secret_key)


# --------------------------------------------------------------------------- #
# Market clock
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime

    def minutes_to_close(self) -> float:
        return (self.next_close - self.timestamp).total_seconds() / 60.0

    def seconds_to_next_open(self) -> float:
        return max((self.next_open - self.timestamp).total_seconds(), 0.0)


def get_market_clock(trading_client) -> MarketClock:
    """Read the authoritative market clock from Alpaca (handles holidays)."""
    clock = trading_client.get_clock()
    return MarketClock(
        timestamp=clock.timestamp,
        is_open=bool(clock.is_open),
        next_open=clock.next_open,
        next_close=clock.next_close,
    )


# --------------------------------------------------------------------------- #
# Minute bars
# --------------------------------------------------------------------------- #
def fetch_minute_bars(
    data_client,
    symbols: list[str],
    lookback_minutes: int = 120,
    feed: str = "iex",
) -> pd.DataFrame:
    """Return recent 1-minute bars as a long DataFrame.

    Columns: symbol, timestamp, open, high, low, close, volume, vwap.
    ``feed`` is ``iex`` (free) or ``sip`` (paid market-data subscription).
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Minute,
        start=datetime.now(UTC) - timedelta(minutes=lookback_minutes + 5),
        feed=feed,
    )
    bars = data_client.get_stock_bars(request)
    frame = bars.df
    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"]
        )

    frame = frame.reset_index().rename(columns={"vwap": "vwap"})
    keep = ["symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"]
    for column in keep:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[keep].sort_values(["symbol", "timestamp"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Baseline intraday signal
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IntradaySignalConfig:
    momentum_minutes: int = 15
    vwap_weight: float = 0.5
    momentum_weight: float = 0.5
    top_n: int = 5
    min_bars: int = 20


def compute_intraday_features(bars: pd.DataFrame, config: IntradaySignalConfig) -> pd.DataFrame:
    """Per-symbol intraday features from minute bars.

    Returns one row per symbol with: close, vwap, vwap_gap, momentum, and the
    20-day-style ``volatility_20d`` column name the risk filter expects (here it
    is an intraday realised vol proxy so the existing guardrails still apply).
    """
    rows = []
    for symbol, group in bars.groupby("symbol"):
        group = group.sort_values("timestamp")
        if len(group) < config.min_bars:
            continue
        close = float(group["close"].iloc[-1])
        vwap_series = group["vwap"].astype(float)
        vwap = float(vwap_series.iloc[-1]) if vwap_series.notna().any() else close
        window = group["close"].astype(float).tail(config.momentum_minutes + 1)
        momentum = float(window.iloc[-1] / window.iloc[0] - 1.0) if len(window) >= 2 else 0.0
        returns = group["close"].astype(float).pct_change().dropna()
        intraday_vol = float(returns.tail(60).std()) if not returns.empty else 0.0
        rows.append(
            {
                "symbol": symbol,
                "close": close,
                "vwap": vwap,
                "vwap_gap": (close - vwap) / vwap if vwap else 0.0,
                "momentum": momentum,
                "volatility_20d": intraday_vol,
            }
        )
    return pd.DataFrame(rows)


def rank_to_targets(features: pd.DataFrame, config: IntradaySignalConfig) -> pd.DataFrame:
    """Blend momentum and VWAP-gap ranks into long-only target weights.

    Output columns mirror ``reports/latest_recommendations.csv`` so the existing
    risk-control and order-plan code can consume it unchanged:
    symbol, close, volatility_20d, target_weight.
    """
    if features.empty:
        return features.assign(target_weight=pd.Series(dtype=float))

    ranked = features.copy()
    ranked["momentum_rank"] = ranked["momentum"].rank(pct=True)
    ranked["vwap_rank"] = ranked["vwap_gap"].rank(pct=True)
    ranked["score"] = (
        config.momentum_weight * ranked["momentum_rank"]
        + config.vwap_weight * ranked["vwap_rank"]
    )
    # Only go long names with positive intraday momentum.
    ranked = ranked[ranked["momentum"] > 0]
    ranked = ranked.sort_values("score", ascending=False).head(config.top_n)
    if ranked.empty:
        return ranked.assign(target_weight=pd.Series(dtype=float))

    weight = 0.95 / len(ranked)
    ranked["target_weight"] = weight
    return ranked[["symbol", "close", "volatility_20d", "target_weight"]].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Alerting
# --------------------------------------------------------------------------- #
def send_alert(message: str, webhook_url: str | None = None) -> None:
    """Post a one-line alert to a webhook. No-op if no URL is configured.

    Works with Discord/Slack incoming webhooks (both accept a JSON body with a
    ``content``/``text`` field). Failures are swallowed so alerting never takes
    the trader down.
    """
    url = webhook_url or os.environ.get("ALERT_WEBHOOK_URL")
    stamped = f"[{datetime.now(UTC).isoformat(timespec='seconds')}] {message}"
    print(stamped, flush=True)
    if not url:
        return
    body = json.dumps({"content": stamped, "text": stamped}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=5)
    except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - network
        print(f"alert webhook failed: {exc}", flush=True)
