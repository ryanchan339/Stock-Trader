"""Long-running intraday paper-trading service.

This is the always-on process you host on a server. It loops through the trading
session, and on each bar it:

  1. Checks the Alpaca market clock (honours holidays / early closes).
  2. Enforces a daily-loss kill switch and a flatten-before-close rule.
  3. Pulls recent minute bars, computes a baseline intraday signal, and turns the
     top names into target weights.
  4. Reuses the EXISTING risk controls and order-plan logic from
     ``alpaca_paper_trade.py`` to size and (optionally) submit paper orders.
  5. Appends an audit record and emits a heartbeat/alert.

Safety defaults: dry-run (no orders submitted) and paper only. You must pass
``--submit`` to send paper orders, and the script refuses to run against live
credentials unless ``--allow-live`` is also passed.

  Dry run:  .venv/bin/python scripts/live_trader.py --universe starter
  Submit:   .venv/bin/python scripts/live_trader.py --universe starter --submit

This baseline signal is a placeholder, not a validated edge. Backtest an
intraday model and swap it into ``rank_to_targets`` before trusting it.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from stock_trader.env import load_dotenv
from stock_trader.intraday import (
    IntradaySignalConfig,
    build_data_client,
    build_trading_client,
    compute_intraday_features,
    credentials_from_env,
    fetch_minute_bars,
    get_market_clock,
    rank_to_targets,
    send_alert,
)
from stock_trader.universes import get_universe

# Reuse the battle-tested risk + order-plan logic instead of duplicating it.
from alpaca_paper_trade import (  # type: ignore
    RiskControls,
    append_jsonl,
    apply_risk_controls,
    build_order_plan,
    filter_recommendations,
    submit_orders,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the intraday paper-trading service.")
    parser.add_argument("--universe", default="starter")
    parser.add_argument("--symbols", nargs="*", help="Explicit symbols; overrides --universe.")
    parser.add_argument("--feed", default="iex", choices=["iex", "sip"])
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--bar-seconds", type=int, default=60, help="Seconds between decision cycles.")
    parser.add_argument("--lookback-minutes", type=int, default=120)
    # Intraday signal blend.
    parser.add_argument("--momentum-minutes", type=int, default=15)
    parser.add_argument("--momentum-weight", type=float, default=0.5)
    parser.add_argument("--vwap-weight", type=float, default=0.5)
    # Risk controls (mirror alpaca_paper_trade defaults).
    parser.add_argument("--max-position-weight", type=float, default=0.15)
    parser.add_argument("--max-gross-exposure", type=float, default=0.75)
    parser.add_argument("--min-price", type=float, default=5.0)
    parser.add_argument("--max-volatility-20d", type=float, default=None,
                        help="Intraday realised-vol cap; default off for intraday.")
    parser.add_argument("--min-notional", type=float, default=25.0)
    # Daytrading guardrails.
    parser.add_argument("--max-daily-loss-pct", type=float, default=0.02,
                        help="Flatten and halt for the day if equity falls this fraction below the day's open.")
    parser.add_argument("--flatten-before-close-min", type=float, default=10.0,
                        help="Minutes before the close to exit all positions (flat overnight).")
    parser.add_argument("--max-day-trades", type=int, default=3,
                        help="Stop opening new positions after this many day-trades (PDT guard).")
    # Modes.
    parser.add_argument("--submit", action="store_true", help="Actually submit paper orders.")
    parser.add_argument("--allow-live", action="store_true", help="Permit running against live (non-paper) keys.")
    parser.add_argument("--once", action="store_true", help="Run a single decision cycle then exit.")
    parser.add_argument("--alert-webhook", default=None, help="Override ALERT_WEBHOOK_URL.")
    parser.add_argument("--log", default="reports/live_trading_log.jsonl")
    return parser.parse_args()


def resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [symbol.upper() for symbol in args.symbols]
    return get_universe(args.universe)


def fetch_positions(trading_client) -> dict[str, dict[str, float]]:
    return {
        position.symbol: {
            "qty": float(position.qty),
            "market_value": float(position.market_value),
            "current_price": float(position.current_price),
        }
        for position in trading_client.get_all_positions()
    }


def run_cycle(args, trading_client, data_client, symbols, signal_config, risk_controls, webhook) -> dict:
    """One decision cycle. Returns the audit payload."""
    account = trading_client.get_account()
    equity = float(account.equity)
    day_trades = int(getattr(account, "daytrade_count", 0) or 0)

    bars = fetch_minute_bars(data_client, symbols, args.lookback_minutes, feed=args.feed)
    features = compute_intraday_features(bars, signal_config)
    recommendations = rank_to_targets(features, signal_config)

    pdt_blocked = day_trades >= args.max_day_trades
    if pdt_blocked:
        # Do not open/raise new exposure; only exits will be allowed below.
        recommendations = recommendations.iloc[0:0]

    if recommendations.empty:
        eligible, excluded = {}, []
        adjusted = {}
        latest_prices = {}
    else:
        eligible, excluded = filter_recommendations(recommendations, risk_controls)
        adjusted = apply_risk_controls(
            targets=eligible,
            controls=risk_controls,
            market_regime={"risk_off": False},
        )
        latest_prices = {
            row.symbol: float(row.close) for row in recommendations.itertuples(index=False)
        }

    positions = fetch_positions(trading_client)
    # Ensure held names still have a price for sells even when not re-recommended.
    for symbol, position in positions.items():
        latest_prices.setdefault(symbol, position["current_price"])

    orders = build_order_plan(
        targets=adjusted,
        latest_prices=latest_prices,
        positions=positions,
        equity=equity,
        min_notional=args.min_notional,
    )

    payload = {
        "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "submit" if args.submit else "dry_run",
        "equity": equity,
        "daytrade_count": day_trades,
        "pdt_blocked": pdt_blocked,
        "adjusted_targets": adjusted,
        "excluded_recommendations": excluded,
        "orders": [order.__dict__ for order in orders],
    }
    if args.submit and orders:
        payload["submitted_orders"] = submit_orders(trading_client, orders)

    append_jsonl(Path(args.log), payload)
    if orders:
        send_alert(
            f"cycle: equity=${equity:,.0f} dt={day_trades} "
            f"{'submitted' if args.submit else 'planned'} {len(orders)} orders",
            webhook,
        )
    return payload


def flatten_all(trading_client, args, webhook, reason: str) -> None:
    send_alert(f"FLATTEN ({reason})", webhook)
    if not args.submit:
        return
    try:
        trading_client.close_all_positions(cancel_orders=True)
    except Exception as exc:  # pragma: no cover - network/runtime
        send_alert(f"flatten failed: {exc}", webhook)


def main() -> None:
    args = parse_args()
    load_dotenv()
    webhook = args.alert_webhook

    creds = credentials_from_env()
    if not creds.has_keys:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required.")
    if not creds.paper and not args.allow_live:
        raise SystemExit("Refusing to run against live keys without --allow-live. Set ALPACA_PAPER=true for paper.")

    trading_client = build_trading_client(creds)
    data_client = build_data_client(creds)
    symbols = resolve_symbols(args)
    signal_config = IntradaySignalConfig(
        momentum_minutes=args.momentum_minutes,
        vwap_weight=args.vwap_weight,
        momentum_weight=args.momentum_weight,
        top_n=args.top_n,
    )
    risk_controls = RiskControls(
        max_position_weight=args.max_position_weight,
        max_gross_exposure=args.max_gross_exposure,
        min_price=args.min_price,
        max_volatility_20d=args.max_volatility_20d,
        risk_off_spy_ma_days=0,
        risk_off_exposure_multiplier=1.0,
    )

    send_alert(
        f"live_trader starting: {'SUBMIT' if args.submit else 'DRY-RUN'} "
        f"paper={creds.paper} universe={len(symbols)} symbols top_n={args.top_n}",
        webhook,
    )

    day_open_equity: float | None = None
    halted_for_day = False
    last_session_date = None

    while True:
        clock = get_market_clock(trading_client)
        session_date = clock.timestamp.astimezone().date()

        # New session: reset daily kill switch state.
        if session_date != last_session_date:
            last_session_date = session_date
            halted_for_day = False
            day_open_equity = None

        if not clock.is_open:
            if args.once:
                send_alert("market closed; --once with closed market, exiting.", webhook)
                return
            sleep_for = min(clock.seconds_to_next_open(), 3600)
            send_alert(f"market closed; sleeping {sleep_for/60:.0f}m until next open.", webhook)
            time.sleep(max(sleep_for, args.bar_seconds))
            continue

        if day_open_equity is None:
            day_open_equity = float(trading_client.get_account().equity)
            send_alert(f"session open; day_open_equity=${day_open_equity:,.0f}", webhook)

        # Flatten-before-close rule (flat overnight).
        if clock.minutes_to_close() <= args.flatten_before_close_min:
            flatten_all(trading_client, args, webhook, "near close")
            halted_for_day = True
            if args.once:
                return
            time.sleep(min(clock.minutes_to_close() * 60 + 30, 300))
            continue

        if halted_for_day:
            time.sleep(args.bar_seconds)
            continue

        # Daily-loss kill switch.
        current_equity = float(trading_client.get_account().equity)
        drawdown = (day_open_equity - current_equity) / day_open_equity if day_open_equity else 0.0
        if drawdown >= args.max_daily_loss_pct:
            flatten_all(trading_client, args, webhook,
                        f"daily loss {drawdown:.2%} >= {args.max_daily_loss_pct:.2%}")
            halted_for_day = True
            continue

        try:
            run_cycle(args, trading_client, data_client, symbols, signal_config, risk_controls, webhook)
        except Exception as exc:  # pragma: no cover - keep the service alive
            send_alert(f"cycle error: {exc!r}", webhook)

        if args.once:
            return
        time.sleep(args.bar_seconds)


if __name__ == "__main__":
    main()
