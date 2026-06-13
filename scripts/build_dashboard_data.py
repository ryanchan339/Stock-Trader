"""Build the static dashboard data file from committed report artifacts.

Reads the reports the weekly reweight already produces and emits a single
``docs/data/dashboard.json`` consumed by the GitHub Pages dashboard. Pure
read-only over committed files, so it is safe to run in CI on every reweight.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
OUT = ROOT / "docs" / "data" / "dashboard.json"

BASELINE_LABELS = {
    "momentum_20d": "Momentum 20d",
    "relative_strength_20d": "Relative strength 20d",
    "low_volatility_20d": "Low volatility 20d",
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


def main() -> None:
    metrics = read_json(REPORTS / "metrics.json")
    plan = read_json(REPORTS / "latest_order_plan.json")
    log = read_jsonl(REPORTS / "paper_trading_log.jsonl")

    classification = metrics.get("classification", {})
    backtest = metrics.get("backtest", {})

    baselines = {}
    for key, label in BASELINE_LABELS.items():
        series = read_equity_curve(REPORTS / f"baseline_{key}_equity.csv")
        if series:
            baselines[label] = series

    regime = plan.get("market_regime") or {}
    data = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "current": {
            "equity": plan.get("equity"),
            "as_of": plan.get("created_at"),
            "mode": plan.get("mode"),
            "paper": plan.get("paper"),
            "risk_off": regime.get("risk_off"),
            "holdings": plan.get("adjusted_targets", {}),
        },
        "headline": {
            "test_start": classification.get("test_start"),
            "accuracy": classification.get("accuracy"),
            "roc_auc": classification.get("roc_auc"),
            "score_mode": classification.get("score_mode"),
            "total_return": backtest.get("total_return"),
            "benchmark_return": backtest.get("benchmark_return"),
            "excess_return": backtest.get("excess_return"),
            "max_drawdown": backtest.get("max_drawdown"),
            "sharpe": backtest.get("sharpe"),
        },
        "live": build_live_series(log),
        "backtest": {
            "strategy": read_equity_curve(REPORTS / "equity_curve.csv"),
            "baselines": baselines,
        },
        "orders": build_orders(log),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)} "
          f"({len(data['live'])} live points, {len(data['orders'])} order events)")


if __name__ == "__main__":
    main()
