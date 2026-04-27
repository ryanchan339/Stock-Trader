from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1))
warnings.filterwarnings("ignore", category=UserWarning, module="joblib.*")

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.backtest import BacktestConfig, run_rank_backtest
from stock_trader.features import load_price_data, make_model_frame
from stock_trader.modeling import build_model, evaluate_scores, fit_model, score_frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run yearly walk-forward validation.")
    parser.add_argument("--data", default="data/raw/yahoo/all_symbols.csv")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--first-test-year", type=int, default=2022)
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
    max_year = int(frame["date"].dt.year.max())
    rows = []
    scored_frames = []

    for test_year in range(args.first_test_year, max_year + 1):
        train = frame[frame["date"].dt.year < test_year].copy()
        test = frame[frame["date"].dt.year == test_year].copy()
        if train.empty or test.empty:
            continue

        model = fit_model(build_model(random_state=42), train)
        scored = score_frame(model, test)
        evaluation = evaluate_scores(test, scored["score"])
        equity, trades, backtest = run_rank_backtest(
            scored=scored,
            prices=prices,
            benchmark=args.benchmark,
            config=BacktestConfig(top_n=args.top_n, rebalance_every=args.rebalance_every),
        )

        rows.append(
            {
                "test_year": test_year,
                **evaluation.as_dict(),
                "train_rows": len(train),
                "test_rows": len(test),
                **{f"backtest_{key}": value for key, value in backtest.items()},
                "trade_count": len(trades),
            }
        )
        scored["test_year"] = test_year
        scored_frames.append(scored)
        equity.to_csv(reports_dir / f"walk_forward_equity_{test_year}.csv", index=False)
        trades.to_csv(reports_dir / f"walk_forward_trades_{test_year}.csv", index=False)

    summary = pd.DataFrame(rows)
    if summary.empty:
        raise SystemExit("No walk-forward folds were produced.")

    combined_scores = pd.concat(scored_frames, ignore_index=True)
    summary.to_csv(reports_dir / "walk_forward_summary.csv", index=False)
    combined_scores.to_csv(reports_dir / "walk_forward_scores.csv", index=False)

    aggregate = {
        "folds": int(len(summary)),
        "mean_roc_auc": float(summary["roc_auc"].mean()),
        "mean_total_return": float(summary["backtest_total_return"].mean()),
        "mean_excess_return": float(summary["backtest_excess_return"].mean()),
        "worst_max_drawdown": float(summary["backtest_max_drawdown"].min()),
        "positive_excess_folds": int((summary["backtest_excess_return"] > 0).sum()),
    }
    (reports_dir / "walk_forward_metrics.json").write_text(
        json.dumps({"aggregate": aggregate, "folds": rows}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"aggregate": aggregate, "folds": rows}, indent=2))


if __name__ == "__main__":
    main()
