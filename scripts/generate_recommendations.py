from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.features import FEATURE_COLUMNS, add_technical_features, load_price_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate latest target portfolio recommendations.")
    parser.add_argument("--data", default="data/raw/yahoo/all_symbols.csv")
    parser.add_argument("--model", default="models/stock_ranker.joblib")
    parser.add_argument(
        "--strategy",
        choices=["model", "model_momentum_blend", "momentum_20d"],
        default="model_momentum_blend",
    )
    parser.add_argument("--model-weight", type=float, default=None)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--portfolio-allocation", type=float, default=0.95)
    parser.add_argument("--out", default="reports/latest_recommendations.csv")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prices = load_price_data(args.data)
    frame = add_technical_features(prices, benchmark=args.benchmark)
    frame = frame[frame["symbol"] != args.benchmark].copy()
    frame = frame.replace([np.inf, -np.inf], np.nan)

    latest_date = frame["date"].max()
    latest = frame[frame["date"] == latest_date].dropna(subset=FEATURE_COLUMNS).copy()
    if latest.empty:
        raise SystemExit("No latest rows available after feature generation.")

    if args.strategy in {"model", "model_momentum_blend"}:
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            with contextlib.redirect_stderr(devnull):
                artifact = joblib.load(args.model)
                latest["model_score"] = artifact["model"].predict_proba(latest[FEATURE_COLUMNS])[:, 1]
        if args.strategy == "model":
            latest["score"] = latest["model_score"]
        else:
            model_weight = args.model_weight
            if model_weight is None:
                model_weight = float(artifact.get("model_weight", 0.25))
            latest["score"] = (
                model_weight * latest["model_score"].rank(pct=True)
                + (1 - model_weight) * latest["return_20d"].rank(pct=True)
            )
    else:
        latest["model_score"] = np.nan
        latest["score"] = latest["return_20d"]

    picks = latest.sort_values("score", ascending=False).head(args.top_n).copy()
    picks["rank"] = range(1, len(picks) + 1)
    picks["strategy"] = args.strategy
    picks["target_weight"] = args.portfolio_allocation / len(picks)
    picks = picks[
        [
            "date",
            "strategy",
            "rank",
            "symbol",
            "score",
            "close",
            "target_weight",
            "return_20d",
            "relative_return_20d",
            "volatility_20d",
        ]
    ]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    picks.to_csv(out_path, index=False)
    print(f"Wrote {len(picks)} recommendations to {out_path}")
    print(picks[["rank", "symbol", "score", "close", "target_weight"]].to_string(index=False))


if __name__ == "__main__":
    main()
