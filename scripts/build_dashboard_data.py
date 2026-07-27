"""Build the static dashboard data file from committed report artifacts.

Reads the reports the weekly reweight already produces and emits a single
``docs/data/dashboard.json`` consumed by the GitHub Pages dashboard. Pure
read-only over committed files, so it is safe to run in CI on every reweight.

The dashboard also renders an explainer section, so this file deliberately
exports the strategy configuration (universe size, horizon, blend weight, risk
controls) alongside the results. Every number the site quotes comes from here
rather than being hard-coded in the page, so the explainer cannot drift away
from what the pipeline actually did.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# universes.py is stdlib-only on purpose, so this script keeps its "read committed
# files, no heavy deps" property and can run anywhere the repo is checked out.
from stock_trader.universes import LARGE_CAP_UNIVERSE

REPORTS = ROOT / "reports"
OUT = ROOT / "docs" / "data" / "dashboard.json"

BENCHMARK = "SPY"

BASELINE_LABELS = {
    "momentum_20d": "Momentum 20d",
    "relative_strength_20d": "Relative strength 20d",
    "low_volatility_20d": "Low volatility 20d",
}

# Plain-language blurbs for the baseline strategies, shown next to their numbers
# so the comparison table is readable without knowing the codebase.
BASELINE_BLURBS = {
    "momentum_20d": "Buy the 5 stocks that rose the most over the last 20 trading days.",
    "relative_strength_20d": "Buy the 5 stocks that beat SPY by the most over the last 20 trading days.",
    "low_volatility_20d": "Buy the 5 calmest stocks — the ones whose daily moves were smallest.",
}

# Columns copied out of the recommendation CSVs, with the cast used to read them.
PICK_FIELDS = {
    "rank": int,
    "symbol": str,
    "score": float,
    "close": float,
    "target_weight": float,
    "return_20d": float,
    "relative_return_20d": float,
    "volatility_20d": float,
}


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def read_equity_curve(path: Path) -> list[dict]:
    if not path.exists():
        return []
    series = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                series.append({"date": row["date"], "equity": float(row["equity"])})
            except (KeyError, ValueError):
                continue
    return series


def read_picks(path: Path) -> list[dict]:
    """Read a recommendations CSV into ranked pick records."""
    if not path.exists():
        return []
    picks = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = (row.get("symbol") or "").strip()
            if not symbol:
                continue
            pick = {"date": row.get("date"), "strategy": row.get("strategy")}
            for field, cast in PICK_FIELDS.items():
                try:
                    pick[field] = cast(row[field])
                except (KeyError, TypeError, ValueError):
                    pick[field] = None
            picks.append(pick)
    picks.sort(key=lambda pick: pick["rank"] if pick["rank"] is not None else 10**6)
    return picks


def build_live_series(log: list[dict]) -> list[dict]:
    """One point per market date: equity and the SPY close at that run."""
    by_date: dict[str, dict] = {}
    for record in log:
        regime = record.get("market_regime") or {}
        spy_close = regime.get("close")
        market_date = regime.get("date")
        equity = record.get("equity")
        if spy_close is None or market_date is None or equity is None:
            continue
        # Later records for the same market date overwrite earlier ones, so a
        # submit run supersedes its paired dry run.
        by_date[market_date] = {
            "date": market_date,
            "equity": float(equity),
            "spy": float(spy_close),
        }
    return [by_date[d] for d in sorted(by_date)]


def build_orders(log: list[dict]) -> list[dict]:
    """Flatten every run's orders into a timeline, newest first."""
    events = []
    for record in log:
        created_at = record.get("created_at")
        mode = record.get("mode", "dry_run")
        # Submitted orders are real placements; dry-run orders are plans only.
        submitted = record.get("submitted_orders")
        orders = submitted if submitted else record.get("orders", [])
        placed = bool(submitted)
        for order in orders or []:
            events.append(
                {
                    "time": created_at,
                    "placed": placed,
                    "mode": mode,
                    "symbol": order.get("symbol"),
                    "side": order.get("side"),
                    "qty": order.get("qty"),
                    "notional": order.get("notional"),
                    "reason": order.get("reason"),
                }
            )
    events.sort(key=lambda event: (event.get("time") or ""), reverse=True)
    return events


def build_baseline_metrics(raw: dict) -> list[dict]:
    """Baseline metrics as a display-ordered list with plain-language blurbs."""
    rows = []
    for key, label in BASELINE_LABELS.items():
        metrics = raw.get(key)
        if not metrics:
            continue
        rows.append({"key": key, "label": label, "blurb": BASELINE_BLURBS.get(key, ""), **metrics})
    return rows


def build_strategy(classification: dict, plan: dict, picks: list[dict]) -> dict:
    """Configuration the explainer quotes, resolved from committed artifacts."""
    universe = [symbol for symbol in LARGE_CAP_UNIVERSE if symbol != BENCHMARK]

    # top_n / rebalance_every are recorded by newer training runs; fall back to
    # what the current recommendations file implies for older metrics.json files.
    top_n = classification.get("top_n") or len(picks) or None
    allocation = None
    if picks:
        weights = [pick["target_weight"] for pick in picks if pick["target_weight"] is not None]
        if weights:
            allocation = round(sum(weights), 6)

    return {
        "benchmark": BENCHMARK,
        "universe": universe,
        "universe_size": len(universe),
        "feature_count": classification.get("feature_count"),
        "horizon_days": classification.get("horizon_days"),
        "test_start": classification.get("test_start"),
        "train_rows": classification.get("train_rows"),
        "test_rows": classification.get("test_rows"),
        "model_preset": classification.get("model_preset"),
        "score_mode": classification.get("score_mode"),
        "model_weight": classification.get("model_weight"),
        "momentum_weight": (
            None if classification.get("model_weight") is None
            else round(1.0 - float(classification["model_weight"]), 6)
        ),
        "top_n": top_n,
        "rebalance_every": classification.get("rebalance_every") or 5,
        "portfolio_allocation": allocation,
        "raw_target_weight": (
            round(allocation / top_n, 6) if allocation and top_n else None
        ),
        "risk_controls": plan.get("risk_controls") or {},
        "min_notional": 25.0,
    }


def main() -> None:
    metrics = read_json(REPORTS / "metrics.json")
    plan = read_json(REPORTS / "latest_order_plan.json")
    log = read_jsonl(REPORTS / "paper_trading_log.jsonl")
    walk_forward = read_json(REPORTS / "walk_forward_metrics.json")

    classification = metrics.get("classification", {})
    backtest = metrics.get("backtest", {})

    picks = read_picks(REPORTS / "latest_recommendations.csv")
    momentum_picks = read_picks(REPORTS / "latest_momentum_recommendations.csv")

    baselines = {}
    for key, label in BASELINE_LABELS.items():
        series = read_equity_curve(REPORTS / f"baseline_{key}_equity.csv")
        if series:
            baselines[label] = series

    regime = plan.get("market_regime") or {}
    data = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "strategy": build_strategy(classification, plan, picks),
        "current": {
            "equity": plan.get("equity"),
            "as_of": plan.get("created_at"),
            "market_date": regime.get("date") or (picks[0]["date"] if picks else None),
            "mode": plan.get("mode"),
            "paper": plan.get("paper"),
            "risk_off": regime.get("risk_off"),
            "market_regime": regime,
            "holdings": plan.get("adjusted_targets", {}),
            "picks": picks,
            "momentum_picks": momentum_picks,
            "excluded": plan.get("excluded_recommendations", []),
        },
        "headline": {
            "test_start": classification.get("test_start"),
            "accuracy": classification.get("accuracy"),
            "precision": classification.get("precision"),
            "roc_auc": classification.get("roc_auc"),
            "score_mode": classification.get("score_mode"),
            "total_return": backtest.get("total_return"),
            "benchmark_return": backtest.get("benchmark_return"),
            "excess_return": backtest.get("excess_return"),
            "max_drawdown": backtest.get("max_drawdown"),
            "sharpe": backtest.get("sharpe"),
            "ending_equity": backtest.get("ending_equity"),
        },
        "live": build_live_series(log),
        "backtest": {
            "strategy": read_equity_curve(REPORTS / "equity_curve.csv"),
            "baselines": baselines,
            "baseline_metrics": build_baseline_metrics(read_json(REPORTS / "baseline_metrics.json")),
        },
        "walk_forward": {
            "aggregate": walk_forward.get("aggregate", {}),
            "folds": walk_forward.get("folds", []),
        },
        "orders": build_orders(log),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} "
          f"({len(data['live'])} live points, {len(data['orders'])} order events, "
          f"{len(data['walk_forward']['folds'])} walk-forward folds)")


if __name__ == "__main__":
    main()
