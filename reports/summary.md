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
Accuracy:  0.5082
Precision: 0.4967
ROC AUC:   0.5111
```

## Ranking Backtest

```text
Starting equity:   $100,000.00
Ending equity:     $198,987.27
Total return:      98.99%
SPY return:        54.07%
Excess return:     44.91%
Max drawdown:      -16.81%
Sharpe ratio:      1.37
Score mode:        25% ML rank / 75% 20-day momentum rank
```

## Walk-Forward Validation

```text
Folds:                  5 yearly folds, 2022-2026
Mean yearly return:     15.75%
Mean yearly excess:     4.61%
Positive excess folds:  4 of 5
Worst max drawdown:     -32.34%
Mean ROC AUC:           0.5060
```

## Interpretation

The classifier is only slightly better than random, so the production score is
not pure model probability. It blends model rank with 20-day momentum rank. This
improved the recent backtest and kept yearly walk-forward validation positive in
4 of 5 folds.

The 20-day momentum baseline remains strong. Paper testing should track both the
blended ML recommendations and the raw momentum baseline.

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
