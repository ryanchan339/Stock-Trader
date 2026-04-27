# Stock Trader

Local research pipeline for a machine-learning stock ranking strategy.

## Download Data

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/download_yahoo_data.py
```

By default, the script downloads the `large_cap` universe. To use the original
small test set:

```bash
.venv/bin/python scripts/download_yahoo_data.py --universe starter
```

Output is written to `data/raw/yahoo/`.

## Train Model And Backtest

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/train_model.py
```

The training script:

- Builds technical features from Yahoo OHLCV data.
- Labels each stock by whether it outperforms `SPY` over the next 5 trading days.
- Trains a `HistGradientBoostingClassifier`.
- Backtests a top-ranked long-only portfolio from `2024-01-01` onward.

Outputs:

```text
models/stock_ranker.joblib
reports/model_frame.csv
reports/test_scores.csv
reports/equity_curve.csv
reports/trades.csv
reports/metrics.json
```

## Latest Model Picks

```bash
.venv/bin/python scripts/predict_latest.py
```

This scores the latest available date in `data/raw/yahoo/all_symbols.csv`.

## Validation

```bash
.venv/bin/python scripts/walk_forward.py
.venv/bin/python scripts/baselines.py
```

Walk-forward validation trains on prior years and tests one year at a time. The
baseline script compares the ML strategy against simple non-ML strategies like
20-day momentum.

## Paper Trading Prep

Generate recommendations:

```bash
.venv/bin/python scripts/generate_recommendations.py --strategy model_momentum_blend --top-n 5
.venv/bin/python scripts/generate_recommendations.py --strategy momentum_20d --top-n 5 --out reports/latest_momentum_recommendations.csv
```

The default paper-test score is a blended rank: 25% ML model probability and
75% 20-day momentum. Raw model scores are still available with
`--strategy model`, and the baseline is available with `--strategy momentum_20d`.
During paper testing, keep generating both `reports/latest_recommendations.csv`
and `reports/latest_momentum_recommendations.csv` so the traded blend can be
compared against the plain momentum baseline.

Refresh data and retrain before a paper-trading run:

```bash
.venv/bin/python scripts/download_yahoo_data.py
.venv/bin/python scripts/train_model.py
.venv/bin/python scripts/generate_recommendations.py --strategy model_momentum_blend --top-n 5
.venv/bin/python scripts/generate_recommendations.py --strategy momentum_20d --top-n 5 --out reports/latest_momentum_recommendations.csv
```

Generate a dry-run Alpaca order plan:

```bash
.venv/bin/python scripts/alpaca_paper_trade.py --recommendations reports/latest_recommendations.csv
```

To connect Alpaca paper trading, create `.env` from `.env.example`:

```text
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_PAPER=true
```

Then run the same command again without `--submit` first. Only after reviewing
the generated order plan should you submit paper orders:

```bash
.venv/bin/python scripts/alpaca_paper_trade.py --recommendations reports/latest_recommendations.csv --submit
```

The script uses market orders against Alpaca paper trading and defaults to dry
run mode.

The paper trading script applies conservative execution guardrails by default:

```text
max position weight: 15%
max gross exposure: 75%
minimum stock price: $5
maximum 20-day volatility: 6%
risk-off filter: reduce exposure by 50% when SPY is below its 100-day moving average
```

You can tune those controls per run:

```bash
.venv/bin/python scripts/alpaca_paper_trade.py \
  --recommendations reports/latest_recommendations.csv \
  --max-position-weight 0.10 \
  --max-gross-exposure 0.50 \
  --min-price 5 \
  --max-volatility-20d 0.06 \
  --risk-off-spy-ma-days 100 \
  --risk-off-exposure-multiplier 0.50
```

Every dry run and submit run writes the latest plan to
`reports/latest_order_plan.json` and appends an audit record to
`reports/paper_trading_log.jsonl`.

## Current Prototype Notes

This is research code, not financial advice and not ready for real-money automation.
The default universe is now a curated liquid US large-cap set, with `SPY` used as
the benchmark. It is not an official index membership list; it is a research
universe meant to be broad enough for better model testing.

The next serious upgrade is forward paper testing with daily logs before any
real-money use.
