from __future__ import annotations

import argparse
import contextlib
import os
import sys
import warnings
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 1))
warnings.filterwarnings("ignore", message="Could not find the number of physical cores.*")
warnings.filterwarnings("ignore", category=UserWarning, module="joblib.*")

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.features import FEATURE_COLUMNS, add_technical_features, load_price_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score the latest available row for each stock.")
    parser.add_argument("--data", default="data/raw/yahoo/all_symbols.csv")
    parser.add_argument("--model", default="models/stock_ranker.joblib")
    parser.add_argument("--top-n", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stderr(devnull):
            artifact = joblib.load(args.model)
    prices = load_price_data(args.data)
    frame = add_technical_features(prices, benchmark=artifact["benchmark"])
    frame = frame[frame["symbol"] != artifact["benchmark"]].copy()
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURE_COLUMNS)

    latest_date = frame["date"].max()
    latest = frame[frame["date"] == latest_date].copy()
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stderr(devnull):
            latest["model_score"] = artifact["model"].predict_proba(latest[FEATURE_COLUMNS])[:, 1]
    if artifact.get("score_mode") == "model_momentum_blend":
        model_weight = float(artifact.get("model_weight", 0.25))
        latest["score"] = (
            model_weight * latest["model_score"].rank(pct=True)
            + (1 - model_weight) * latest["return_20d"].rank(pct=True)
        )
    else:
        latest["score"] = latest["model_score"]
    latest = latest.sort_values("score", ascending=False)

    print(f"Latest scored date: {latest_date.date()}")
    print(latest[["symbol", "score", "close"]].head(args.top_n).to_string(index=False))


if __name__ == "__main__":
    main()
