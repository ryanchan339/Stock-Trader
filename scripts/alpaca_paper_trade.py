from __future__ import annotations

import argparse
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or submit Alpaca paper orders from recommendations.")
    parser.add_argument("--recommendations", default="reports/latest_recommendations.csv")
    parser.add_argument("--min-notional", type=float, default=25.0)
    parser.add_argument("--dry-run-equity", type=float, default=100_000.0)
    parser.add_argument("--submit", action="store_true", help="Actually submit paper orders to Alpaca.")
    parser.add_argument("--out", default="reports/latest_order_plan.json")
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
        targets=targets,
        latest_prices=latest_prices,
        positions=positions,
        equity=equity,
        min_notional=args.min_notional,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": "submit" if args.submit else "dry_run",
        "paper": paper,
        "has_alpaca_keys": has_keys,
        "equity": equity,
        "orders": [asdict(order) for order in orders],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))

    if args.submit:
        if not has_keys:
            raise SystemExit("Cannot submit orders: ALPACA_API_KEY and ALPACA_SECRET_KEY are missing.")
        submit_orders(client, orders)


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


def submit_orders(client, orders: list[PlannedOrder]) -> None:
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

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
        print(f"Submitted {order.side} {order.symbol}: {submitted.id}")


if __name__ == "__main__":
    main()
