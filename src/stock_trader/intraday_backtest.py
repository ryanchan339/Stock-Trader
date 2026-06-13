"""Intraday backtest harness.

Replays minute bars and simulates the SAME logic the live service runs:

- The identical signal (``compute_intraday_features`` + ``rank_to_targets``).
- A decision cadence (rebalance every N minutes).
- Long-only target weights, sized against current equity, with slippage.
- Flat-overnight: positions are flattened near each session close.

The point is faithfulness, not a separate model. If you change the signal in
``intraday.py``, this harness re-validates exactly what will trade.

Input bars are a long DataFrame with columns:
    symbol, timestamp (tz-aware), open, high, low, close, volume, vwap
which is what ``scripts/stream_intraday.py snapshot`` writes.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from stock_trader.intraday import (
    MARKET_TZ,
    IntradaySignalConfig,
    compute_intraday_features,
    rank_to_targets,
)


@dataclass(frozen=True)
class IntradayBacktestConfig:
    initial_cash: float = 100_000.0
    decision_every_minutes: int = 5
    lookback_minutes: int = 120
    slippage_bps: float = 5.0
    min_notional: float = 25.0
    flatten_before_close_minutes: float = 10.0


def _prepare(bars: pd.DataFrame) -> pd.DataFrame:
    bars = bars.copy()
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    bars["session"] = bars["timestamp"].dt.tz_convert(MARKET_TZ).dt.date
    numeric = ["open", "high", "low", "close", "volume", "vwap"]
    for column in numeric:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    return bars.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def run_intraday_backtest(
    bars: pd.DataFrame,
    signal_config: IntradaySignalConfig,
    config: IntradayBacktestConfig,
    benchmark: str = "SPY",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    bars = _prepare(bars)
    slippage = config.slippage_bps / 10_000.0

    cash = config.initial_cash
    positions: dict[str, float] = {}
    trade_rows: list[dict] = []
    equity_rows: list[dict] = []

    for session, session_bars in bars.groupby("session"):
        timestamps = sorted(session_bars["timestamp"].unique())
        if not timestamps:
            continue
        session_close = timestamps[-1]
        flatten_after = session_close - pd.Timedelta(minutes=config.flatten_before_close_minutes)

        for index, now in enumerate(timestamps):
            snapshot = session_bars[session_bars["timestamp"] == now]
            last_close = dict(zip(snapshot["symbol"], snapshot["close"]))

            def mark_equity() -> float:
                held = sum(
                    shares * last_close[symbol]
                    for symbol, shares in positions.items()
                    if symbol in last_close and pd.notna(last_close[symbol])
                )
                return cash + held

            # Flatten near the close: go flat overnight, like live_trader.
            if now >= flatten_after:
                for symbol in list(positions):
                    price = last_close.get(symbol)
                    if price is None or pd.isna(price) or price <= 0:
                        continue
                    shares = positions.pop(symbol)
                    cash += shares * price * (1 - slippage)
                    trade_rows.append(
                        {"timestamp": now, "symbol": symbol, "side": "sell",
                         "shares": shares, "price": price, "reason": "flatten_close"}
                    )
                equity_rows.append({"timestamp": now, "session": session, "equity": mark_equity(),
                                    "cash": cash, "positions": len(positions)})
                continue

            warmed_up = index >= signal_config.min_bars
            decision_bar = index % config.decision_every_minutes == 0
            if warmed_up and decision_bar:
                window_start = now - pd.Timedelta(minutes=config.lookback_minutes)
                window = session_bars[
                    (session_bars["timestamp"] > window_start)
                    & (session_bars["timestamp"] <= now)
                ]
                features = compute_intraday_features(window, signal_config)
                targets = rank_to_targets(features, signal_config)
                target_weights = dict(zip(targets["symbol"], targets["target_weight"]))

                equity = mark_equity()

                # Exit names no longer targeted.
                for symbol in list(positions):
                    if symbol not in target_weights:
                        price = last_close.get(symbol)
                        if price is None or pd.isna(price) or price <= 0:
                            continue
                        shares = positions.pop(symbol)
                        cash += shares * price * (1 - slippage)
                        trade_rows.append(
                            {"timestamp": now, "symbol": symbol, "side": "sell",
                             "shares": shares, "price": price, "reason": "exit_non_target"}
                        )

                # Rebalance toward target weights.
                for symbol, weight in target_weights.items():
                    price = last_close.get(symbol)
                    if price is None or pd.isna(price) or price <= 0:
                        continue
                    desired_shares = (equity * weight) / price
                    delta = desired_shares - positions.get(symbol, 0.0)
                    if abs(delta) * price < config.min_notional:
                        continue
                    if delta > 0:
                        cost = delta * price * (1 + slippage)
                        if cost > cash:
                            delta *= cash / cost if cost else 0.0
                            cost = cash
                        cash -= cost
                        positions[symbol] = positions.get(symbol, 0.0) + delta
                        side = "buy"
                        traded = delta
                    else:
                        traded = abs(delta)
                        cash += traded * price * (1 - slippage)
                        positions[symbol] = positions.get(symbol, 0.0) - traded
                        side = "sell"
                    trade_rows.append(
                        {"timestamp": now, "symbol": symbol, "side": side,
                         "shares": traded, "price": price, "reason": "rebalance"}
                    )

            equity_rows.append({"timestamp": now, "session": session, "equity": mark_equity(),
                                "cash": cash, "positions": len(positions)})

    equity_curve = pd.DataFrame(equity_rows)
    trades = pd.DataFrame(trade_rows)
    metrics = _metrics(equity_curve, bars, benchmark, config.initial_cash)
    return equity_curve, trades, metrics


def _metrics(
    equity_curve: pd.DataFrame,
    bars: pd.DataFrame,
    benchmark: str,
    initial_cash: float,
) -> dict[str, float]:
    if equity_curve.empty:
        return {}

    equity = equity_curve.set_index("timestamp")["equity"]
    total_return = equity.iloc[-1] / initial_cash - 1
    max_drawdown = (equity / equity.cummax() - 1).min()

    # Sharpe on daily-resampled equity, matching the daily backtest convention.
    daily_equity = equity_curve.groupby("session")["equity"].last()
    daily_returns = daily_equity.pct_change().dropna()
    sharpe = 0.0
    if not daily_returns.empty and daily_returns.std():
        sharpe = (daily_returns.mean() / daily_returns.std()) * (252 ** 0.5)
    win_rate = float((daily_returns > 0).mean()) if not daily_returns.empty else 0.0

    benchmark_return = 0.0
    benchmark_bars = bars[bars["symbol"] == benchmark].sort_values("timestamp")
    if not benchmark_bars.empty:
        first = benchmark_bars["close"].iloc[0]
        last = benchmark_bars["close"].iloc[-1]
        if first:
            benchmark_return = last / first - 1

    return {
        "total_return": float(total_return),
        "benchmark_buy_hold_return": float(benchmark_return),
        "excess_return": float(total_return - benchmark_return),
        "max_drawdown": float(max_drawdown),
        "sharpe": float(sharpe),
        "daily_win_rate": win_rate,
        "sessions": int(daily_equity.shape[0]),
        "ending_equity": float(equity.iloc[-1]),
    }
