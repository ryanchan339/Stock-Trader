from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.backtest import BacktestConfig, run_rank_backtest
from stock_trader.features import load_price_data, make_model_frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run simple non-ML baselines.")
    parser.add_argument("--data", default="data/raw/yahoo/all_symbols.csv")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--test-start", default="2024-01-01")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--rebalance-every", type=int, default=5)
    parser.add_argument("--reports-dir", default="reports")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    prices = load_price_data(args.data)
    frame = make_model_frame(prices, horizon=args.horizon, benchmark=args.benchmark)
    test = frame[frame["date"] >= pd.Timestamp(args.test_start)].copy()

    baseline_specs = {
        "momentum_20d": "return_20d",
        "low_volatility_20d": "neg_volatility_20d",
        "relative_strength_20d": "relative_return_20d",
    }
    test["neg_volatility_20d"] = -test["volatility_20d"]

    results = {}
    for name, score_column in baseline_specs.items():
        scored = test[["date", "symbol", "close", "target_outperform_spy", "future_return"]].copy()
        scored["score"] = test[score_column]
        equity, trades, metrics = run_rank_backtest(
            scored=scored,
            prices=prices,
            benchmark=args.benchmark,
            config=BacktestConfig(top_n=args.top_n, rebalance_every=args.rebalance_every),
        )
        results[name] = {**metrics, "trade_count": len(trades)}
        equity.to_csv(reports_dir / f"baseline_{name}_equity.csv", index=False)
        trades.to_csv(reports_dir / f"baseline_{name}_trades.csv", index=False)

    (reports_dir / "baseline_metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
