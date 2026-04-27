from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1))
warnings.filterwarnings("ignore", message="Could not find the number of physical cores.*")
warnings.filterwarnings("ignore", category=UserWarning, module="joblib.*")

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.backtest import BacktestConfig, run_rank_backtest
from stock_trader.features import FEATURE_COLUMNS, load_price_data, make_model_frame
from stock_trader.modeling import build_model, fit_model, score_frame, evaluate_scores


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a stock ranking model and run a simple backtest.")
    parser.add_argument("--data", default="data/raw/yahoo/all_symbols.csv")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--test-start", default="2024-01-01")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--rebalance-every", type=int, default=5)
    parser.add_argument("--models-dir", default="models")
    parser.add_argument("--reports-dir", default="reports")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    models_dir = Path(args.models_dir)
    reports_dir = Path(args.reports_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    prices = load_price_data(args.data)
    frame = make_model_frame(prices, horizon=args.horizon, benchmark=args.benchmark)
    test_start = pd.Timestamp(args.test_start)

    train = frame[frame["date"] < test_start].copy()
    test = frame[frame["date"] >= test_start].copy()
    if train.empty or test.empty:
        raise SystemExit("Train/test split produced an empty set. Adjust --test-start.")

    model = fit_model(build_model(), train)

    scored = score_frame(model, test)
    evaluation = evaluate_scores(test, scored["score"])
    classification = {
        **evaluation.as_dict(),
        "train_rows": int(len(train)),
        "test_start": args.test_start,
        "horizon_days": args.horizon,
    }

    equity, trades, backtest_metrics = run_rank_backtest(
        scored=scored,
        prices=prices,
        benchmark=args.benchmark,
        config=BacktestConfig(top_n=args.top_n, rebalance_every=args.rebalance_every),
    )

    metrics = {"classification": classification, "backtest": backtest_metrics}
    joblib.dump(
        {"model": model, "features": FEATURE_COLUMNS, "benchmark": args.benchmark, "horizon": args.horizon},
        models_dir / "stock_ranker.joblib",
    )
    frame.to_csv(reports_dir / "model_frame.csv", index=False)
    scored.to_csv(reports_dir / "test_scores.csv", index=False)
    equity.to_csv(reports_dir / "equity_curve.csv", index=False)
    trades.to_csv(reports_dir / "trades.csv", index=False)
    (reports_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
