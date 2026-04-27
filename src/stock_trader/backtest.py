from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    top_n: int = 5
    rebalance_every: int = 5
    slippage_bps: float = 5.0


def run_rank_backtest(
    scored: pd.DataFrame,
    prices: pd.DataFrame,
    config: BacktestConfig,
    benchmark: str = "SPY",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    scored = scored.sort_values(["date", "score"], ascending=[True, False]).copy()
    prices = prices.sort_values(["symbol", "date"]).copy()
    close = prices.pivot(index="date", columns="symbol", values="close").sort_index()
    dates = [date for date in sorted(scored["date"].unique()) if date in close.index]

    cash = config.initial_cash
    positions: dict[str, float] = {}
    trade_rows = []
    equity_rows = []
    current_targets: list[str] = []

    for index, date in enumerate(dates):
        available_prices = close.loc[date]
        if index % config.rebalance_every == 0:
            day_scores = scored[scored["date"] == date].nlargest(config.top_n, "score")
            current_targets = day_scores["symbol"].tolist()
            portfolio_value = cash + sum(
                shares * available_prices.get(symbol, 0.0)
                for symbol, shares in positions.items()
                if pd.notna(available_prices.get(symbol))
            )

            for symbol in list(positions):
                if symbol not in current_targets and pd.notna(available_prices.get(symbol)):
                    price = available_prices[symbol]
                    shares = positions.pop(symbol)
                    proceeds = shares * price * (1 - config.slippage_bps / 10_000)
                    cash += proceeds
                    trade_rows.append(
                        {"date": date, "symbol": symbol, "side": "sell", "shares": shares, "price": price}
                    )

            target_value = portfolio_value / max(config.top_n, 1)
            for symbol in current_targets:
                price = available_prices.get(symbol)
                if pd.isna(price) or price <= 0:
                    continue

                current_shares = positions.get(symbol, 0.0)
                desired_shares = target_value / price
                delta = desired_shares - current_shares
                if abs(delta) * price < 25:
                    continue

                if delta > 0:
                    cost = delta * price * (1 + config.slippage_bps / 10_000)
                    if cost > cash:
                        delta *= cash / cost
                        cost = cash
                    cash -= cost
                    positions[symbol] = current_shares + delta
                    side = "buy"
                    shares = delta
                else:
                    shares = abs(delta)
                    proceeds = shares * price * (1 - config.slippage_bps / 10_000)
                    cash += proceeds
                    positions[symbol] = current_shares - shares
                    side = "sell"

                trade_rows.append(
                    {"date": date, "symbol": symbol, "side": side, "shares": shares, "price": price}
                )

        equity = cash + sum(
            shares * available_prices.get(symbol, 0.0)
            for symbol, shares in positions.items()
            if pd.notna(available_prices.get(symbol))
        )
        equity_rows.append({"date": date, "equity": equity, "cash": cash, "positions": len(positions)})

    equity_curve = pd.DataFrame(equity_rows)
    trades = pd.DataFrame(trade_rows)
    metrics = _metrics(equity_curve, close, benchmark, config.initial_cash)
    return equity_curve, trades, metrics


def _metrics(
    equity_curve: pd.DataFrame,
    close: pd.DataFrame,
    benchmark: str,
    initial_cash: float,
) -> dict[str, float]:
    if equity_curve.empty:
        return {}

    equity = equity_curve.set_index("date")["equity"]
    returns = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / initial_cash - 1
    max_drawdown = (equity / equity.cummax() - 1).min()
    sharpe = 0.0
    if returns.std() and pd.notna(returns.std()):
        sharpe = (returns.mean() / returns.std()) * (252**0.5)

    benchmark_return = 0.0
    if benchmark in close:
        benchmark_prices = close.loc[equity.index, benchmark].dropna()
        if not benchmark_prices.empty:
            benchmark_return = benchmark_prices.iloc[-1] / benchmark_prices.iloc[0] - 1

    return {
        "total_return": float(total_return),
        "benchmark_return": float(benchmark_return),
        "excess_return": float(total_return - benchmark_return),
        "max_drawdown": float(max_drawdown),
        "sharpe": float(sharpe),
        "ending_equity": float(equity.iloc[-1]),
    }

