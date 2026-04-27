# Prototype Results

Data source: Yahoo Finance CSVs in `data/raw/yahoo/`

Universe: `large_cap`

Tradeable symbols: 97 stocks

```text
See src/stock_trader/universes.py
```

Benchmark: `SPY`

Training period: data before `2024-01-01`

Test/backtest period: `2024-01-01` through `2026-04-27`

## Classification

```text
Accuracy:  0.5109
Precision: 0.4996
ROC AUC:   0.5158
```

## Ranking Backtest

```text
Starting equity:   $100,000.00
Ending equity:     $165,959.75
Total return:      65.96%
SPY return:        54.07%
Excess return:     11.89%
Max drawdown:      -25.62%
Sharpe ratio:      1.01
```

## Interpretation

The classifier is only slightly better than random on the current single split.
After adding cross-sectional rank features, yearly walk-forward validation beat
SPY in 4 of 5 folds, but 2026 is weak so far.

The 20-day momentum baseline remains strong, so paper testing should track both
the ML recommendations and the momentum baseline.

Treat this as a working paper-trading harness, not a validated live-money
strategy.

## API Readiness

The repo can now generate:

```text
reports/latest_recommendations.csv
reports/latest_order_plan.json
```

The Alpaca connector is dry-run by default. It requires local paper-trading keys
in `.env` before it can read the account or submit paper orders.
