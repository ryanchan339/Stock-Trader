from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.env import get_bool_env, load_dotenv


@dataclass(frozen=True)
class PlannedOrder:
    symbol: str
    side: str
    qty: float | None
    notional: float | None
    reason: str


@dataclass(frozen=True)
class RiskControls:
    max_position_weight: float
    max_gross_exposure: float
    min_price: float
    max_volatility_20d: float | None
    risk_off_spy_ma_days: int
    risk_off_exposure_multiplier: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or submit Alpaca paper orders from recommendations.")
    parser.add_argument("--recommendations", default="reports/latest_recommendations.csv")
    parser.add_argument("--min-notional", type=float, default=25.0)
    parser.add_argument("--dry-run-equity", type=float, default=100_000.0)
    parser.add_argument("--submit", action="store_true", help="Actually submit paper orders to Alpaca.")
    parser.add_argument("--out", default="reports/latest_order_plan.json")
    parser.add_argument("--log", default="reports/paper_trading_log.jsonl")
    parser.add_argument("--market-data", default="data/raw/yahoo/all_symbols.csv")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--max-position-weight", type=float, default=0.15)
    parser.add_argument("--max-gross-exposure", type=float, default=0.75)
    parser.add_argument("--min-price", type=float, default=5.0)
    parser.add_argument("--max-volatility-20d", type=float, default=0.06)
    parser.add_argument("--risk-off-spy-ma-days", type=int, default=100)
    parser.add_argument("--risk-off-exposure-multiplier", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()
    recommendations = pd.read_csv(args.recommendations)
    targets = {
        row.symbol: float(row.target_weight)
        for row in recommendations.itertuples(index=False)
    }
    latest_prices = {
        row.symbol: float(row.close)
        for row in recommendations.itertuples(index=False)
    }
    risk_controls = RiskControls(
        max_position_weight=args.max_position_weight,
        max_gross_exposure=args.max_gross_exposure,
        min_price=args.min_price,
        max_volatility_20d=args.max_volatility_20d,
        risk_off_spy_ma_days=args.risk_off_spy_ma_days,
        risk_off_exposure_multiplier=args.risk_off_exposure_multiplier,
    )
    eligible_targets, excluded_recommendations = filter_recommendations(
        recommendations=recommendations,
        controls=risk_controls,
    )
    market_regime = get_market_regime(
        path=args.market_data,
        benchmark=args.benchmark,
        moving_average_days=args.risk_off_spy_ma_days,
    )
    adjusted_targets = apply_risk_controls(
        targets=eligible_targets,
        controls=risk_controls,
        market_regime=market_regime,
    )

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    paper = get_bool_env("ALPACA_PAPER", default=True)
    has_keys = bool(api_key and secret_key and "replace_me" not in {api_key, secret_key})

    if has_keys:
        account, positions, client = fetch_alpaca_state(api_key, secret_key, paper)
        equity = float(account.equity)
    else:
        client = None
        equity = args.dry_run_equity
        positions = {}

    orders = build_order_plan(
        targets=adjusted_targets,
        latest_prices=latest_prices,
        positions=positions,
        equity=equity,
        min_notional=args.min_notional,
    )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "submit" if args.submit else "dry_run",
        "paper": paper,
        "has_alpaca_keys": has_keys,
        "equity": equity,
        "risk_controls": asdict(risk_controls),
        "market_regime": market_regime,
        "original_targets": targets,
        "eligible_targets": eligible_targets,
        "adjusted_targets": adjusted_targets,
        "excluded_recommendations": excluded_recommendations,
        "positions": positions,
        "orders": [asdict(order) for order in orders],
    }

    if args.submit:
        if not has_keys:
            raise SystemExit("Cannot submit orders: ALPACA_API_KEY and ALPACA_SECRET_KEY are missing.")
        payload["submitted_orders"] = submit_orders(client, orders)

    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    append_jsonl(Path(args.log), payload)
    print(json.dumps(payload, indent=2))


def fetch_alpaca_state(api_key: str, secret_key: str, paper: bool):
    try:
        from alpaca.trading.client import TradingClient
    except ImportError as exc:
        raise SystemExit("alpaca-py is not installed. Run: .venv/bin/python -m pip install -r requirements.txt") from exc

    client = TradingClient(api_key, secret_key, paper=paper)
    account = client.get_account()
    positions = {
        position.symbol: {
            "qty": float(position.qty),
            "market_value": float(position.market_value),
            "current_price": float(position.current_price),
        }
        for position in client.get_all_positions()
    }
    return account, positions, client


def filter_recommendations(
    recommendations: pd.DataFrame,
    controls: RiskControls,
) -> tuple[dict[str, float], list[dict[str, float | str]]]:
    targets = {}
    excluded = []

    for row in recommendations.itertuples(index=False):
        symbol = str(row.symbol)
        close = float(row.close)
        volatility = float(row.volatility_20d)
        target_weight = float(row.target_weight)
        reasons = []

        if close < controls.min_price:
            reasons.append("below_min_price")
        if controls.max_volatility_20d is not None and volatility > controls.max_volatility_20d:
            reasons.append("above_max_volatility_20d")

        if reasons:
            excluded.append(
                {
                    "symbol": symbol,
                    "close": close,
                    "volatility_20d": volatility,
                    "target_weight": target_weight,
                    "reason": ",".join(reasons),
                }
            )
            continue

        targets[symbol] = target_weight

    return targets, excluded


def get_market_regime(path: str, benchmark: str, moving_average_days: int) -> dict[str, float | str | bool | None]:
    if moving_average_days <= 0:
        return {
            "benchmark": benchmark,
            "status": "disabled",
            "risk_off": False,
            "close": None,
            "moving_average": None,
            "moving_average_days": moving_average_days,
        }

    data_path = Path(path)
    if not data_path.exists():
        return {
            "benchmark": benchmark,
            "status": "missing_market_data",
            "risk_off": False,
            "close": None,
            "moving_average": None,
            "moving_average_days": moving_average_days,
        }

    prices = pd.read_csv(data_path, parse_dates=["date"])
    benchmark_prices = prices[prices["symbol"].astype(str).str.upper() == benchmark.upper()].sort_values("date")
    if len(benchmark_prices) < moving_average_days:
        return {
            "benchmark": benchmark,
            "status": "insufficient_market_data",
            "risk_off": False,
            "close": None,
            "moving_average": None,
            "moving_average_days": moving_average_days,
        }

    close = benchmark_prices["close"]
    moving_average = close.rolling(moving_average_days).mean().iloc[-1]
    latest_close = close.iloc[-1]
    risk_off = bool(latest_close < moving_average)
    return {
        "benchmark": benchmark,
        "status": "ok",
        "risk_off": risk_off,
        "date": benchmark_prices["date"].iloc[-1].strftime("%Y-%m-%d"),
        "close": float(latest_close),
        "moving_average": float(moving_average),
        "moving_average_days": moving_average_days,
    }


def apply_risk_controls(
    targets: dict[str, float],
    controls: RiskControls,
    market_regime: dict[str, float | str | bool | None],
) -> dict[str, float]:
    capped_targets = {
        symbol: min(max(weight, 0.0), controls.max_position_weight)
        for symbol, weight in targets.items()
    }

    gross_exposure = sum(capped_targets.values())
    max_gross_exposure = controls.max_gross_exposure
    if market_regime.get("risk_off"):
        max_gross_exposure *= controls.risk_off_exposure_multiplier

    if gross_exposure > max_gross_exposure and gross_exposure > 0:
        scale = max_gross_exposure / gross_exposure
        capped_targets = {
            symbol: weight * scale
            for symbol, weight in capped_targets.items()
        }

    return {
        symbol: round(weight, 6)
        for symbol, weight in capped_targets.items()
        if weight > 0
    }


def build_order_plan(
    targets: dict[str, float],
    latest_prices: dict[str, float],
    positions: dict[str, dict[str, float]],
    equity: float,
    min_notional: float,
) -> list[PlannedOrder]:
    orders: list[PlannedOrder] = []

    for symbol, position in positions.items():
        if symbol not in targets:
            qty = position["qty"]
            if abs(qty * position["current_price"]) >= min_notional:
                orders.append(PlannedOrder(symbol, "sell", qty, None, "exit_non_target"))

    for symbol, target_weight in targets.items():
        target_value = equity * target_weight
        current_value = positions.get(symbol, {}).get("market_value", 0.0)
        delta = target_value - current_value
        if abs(delta) < min_notional:
            continue

        if delta > 0:
            orders.append(PlannedOrder(symbol, "buy", None, round(delta, 2), "raise_to_target"))
        else:
            price = positions.get(symbol, {}).get("current_price") or latest_prices[symbol]
            qty = abs(delta) / price
            orders.append(PlannedOrder(symbol, "sell", round(qty, 6), None, "trim_to_target"))

    return orders


def submit_orders(client, orders: list[PlannedOrder]) -> list[dict[str, str | float | None]]:
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    submitted_orders = []
    for order in orders:
        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL
        request = MarketOrderRequest(
            symbol=order.symbol,
            qty=order.qty,
            notional=order.notional,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        submitted = client.submit_order(order_data=request)
        submitted_orders.append(
            {
                "id": str(submitted.id),
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "notional": order.notional,
                "reason": order.reason,
            }
        )
        print(f"Submitted {order.side} {order.symbol}: {submitted.id}")
    return submitted_orders


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
