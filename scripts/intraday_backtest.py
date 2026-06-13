"""Validate the intraday signal on recorded minute bars before going live.

Reads a minute-bar CSV (as written by ``scripts/stream_intraday.py snapshot``),
replays the exact live signal with flat-overnight behaviour, and writes an
equity curve, trade log, and metrics. Compare ``total_return`` against
``benchmark_buy_hold_return`` AFTER slippage before trusting the signal.

  .venv/bin/python scripts/stream_intraday.py snapshot --universe starter --lookback-minutes 390
  .venv/bin/python scripts/intraday_backtest.py --bars data/intraday/minute_bars.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.intraday import IntradaySignalConfig
from stock_trader.intraday_backtest import IntradayBacktestConfig, run_intraday_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the intraday signal on recorded minute bars.")
    parser.add_argument("--bars", default="data/intraday/minute_bars.csv")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--momentum-minutes", type=int, default=15)
    parser.add_argument("--momentum-weight", type=float, default=0.5)
    parser.add_argument("--vwap-weight", type=float, default=0.5)
    parser.add_argument("--decision-every-minutes", type=int, default=5)
    parser.add_argument("--lookback-minutes", type=int, default=120)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--flatten-before-close-minutes", type=float, default=10.0)
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--reports-dir", default="reports")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bars_path = Path(args.bars)
    if not bars_path.exists():
        raise SystemExit(
            f"No bars at {bars_path}. Record some first:\n"
            "  .venv/bin/python scripts/stream_intraday.py snapshot --universe starter --lookback-minutes 390"
        )
    bars = pd.read_csv(bars_path)
    if bars.empty:
        raise SystemExit(f"{bars_path} is empty.")

    signal_config = IntradaySignalConfig(
        momentum_minutes=args.momentum_minutes,
        vwap_weight=args.vwap_weight,
        momentum_weight=args.momentum_weight,
        top_n=args.top_n,
    )
    backtest_config = IntradayBacktestConfig(
        initial_cash=args.initial_cash,
        decision_every_minutes=args.decision_every_minutes,
        lookback_minutes=args.lookback_minutes,
        slippage_bps=args.slippage_bps,
        flatten_before_close_minutes=args.flatten_before_close_minutes,
    )

    equity, trades, metrics = run_intraday_backtest(
        bars=bars,
        signal_config=signal_config,
        config=backtest_config,
        benchmark=args.benchmark,
    )

    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    equity.to_csv(reports_dir / "intraday_backtest_equity.csv", index=False)
    trades.to_csv(reports_dir / "intraday_backtest_trades.csv", index=False)
    payload = {
        "bars_file": str(bars_path),
        "symbols": int(bars["symbol"].nunique()),
        "trade_count": int(len(trades)),
        "config": {
            "top_n": args.top_n,
            "momentum_minutes": args.momentum_minutes,
            "momentum_weight": args.momentum_weight,
            "vwap_weight": args.vwap_weight,
            "decision_every_minutes": args.decision_every_minutes,
            "lookback_minutes": args.lookback_minutes,
            "slippage_bps": args.slippage_bps,
            "flatten_before_close_minutes": args.flatten_before_close_minutes,
        },
        "metrics": metrics,
    }
    (reports_dir / "intraday_backtest_metrics.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
