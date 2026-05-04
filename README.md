# Stock Trader

Local research and paper-trading pipeline for a long-only US large-cap stock
ranking strategy. The repo downloads market data, builds technical features,
trains a machine-learning ranker, generates target portfolio weights, creates an
Alpaca paper-trading order plan, and records enough reports to track what the
strategy picked and how it is doing.

This is research code, not financial advice, and it is not ready for real-money
automation.

## Current Status

Last refreshed run: `2026-05-04`

Latest traded blend recommendations in `reports/latest_recommendations.csv`:

```text
rank  symbol  target_weight
1     AMZN    19% raw target, capped to 15%
2     GOOGL   19% raw target, capped to 15%
3     AVGO    19% raw target, capped to 15%
4     GOOG    19% raw target, capped to 15%
5     AMD     19% raw target, capped to 15%
```

Latest dry-run Alpaca order plan: `reports/latest_order_plan.json`

```text
run_id: 20260504T205943Z
mode: dry_run
paper equity: $104,396.37
risk_off: false
adjusted targets: AMZN 15%, GOOGL 15%, AVGO 15%, GOOG 15%, AMD 15%
```

Dry-run orders from that plan:

```text
Sell META: 22.318755669 shares
Sell MU: 29.899021449 shares
Buy AMZN: $15,659.46
Sell GOOGL: 2.192608 shares
Buy AVGO: $122.03
Sell GOOG: 2.060491 shares
Buy AMD: $15,659.46
```

Latest model/backtest summary from `reports/metrics.json`:

```text
classification accuracy: 51.03%
classification precision: 49.84%
classification ROC AUC: 51.11%
backtest total return since 2024-01-01: 73.03%
SPY benchmark return: 55.47%
excess return: 17.56%
max drawdown: -19.54%
Sharpe: 1.11
```

Latest walk-forward summary from `reports/walk_forward_metrics.json`:

```text
folds: 5
mean ROC AUC: 50.61%
mean total return: 10.65%
mean excess return: -0.67%
positive excess folds: 2 of 5
worst max drawdown: -34.81%
```

Latest baseline comparison from `reports/baseline_metrics.json`: the plain
`momentum_20d` and `relative_strength_20d` baselines recently outperformed the
blended model in the post-2024 backtest. Keep watching this before trusting the
blend too much.

## What The Repo Does

The pipeline has five main jobs:

- Download adjusted OHLCV data from Yahoo Finance for a curated liquid
  `large_cap` universe plus `SPY`.
- Build technical and cross-sectional features for each symbol/date.
- Train a model to estimate whether each stock will outperform `SPY` over the
  next 5 trading days.
- Rank stocks, pick the top 5, and generate target weights.
- Build a dry-run or submitted Alpaca paper-trading order plan and append an
  audit log for every run.

The default universe is defined in `src/stock_trader/universes.py`. It is a
research universe, not an official index membership list.

## Strategy Defaults

Default operating settings:

```text
benchmark: SPY
prediction horizon: 5 trading days
test start for standard backtest: 2024-01-01
portfolio size: top 5 ranked stocks
rebalance cadence: every 5 trading days
raw portfolio allocation: 95%
raw target per name with 5 picks: 19%
max position weight: 15%
max gross exposure: 75%
minimum stock price: $5
maximum 20-day volatility: 6%
risk-off filter: reduce allowed gross exposure by 50% when SPY is below its 100-day moving average
minimum order notional: $25
```

The backtest rebalances every 5 trading days by default. For paper trading, use
that as the operating cadence: refresh data, retrain, regenerate recommendations,
and create a new dry-run plan once every 5 trading sessions.

## Model And Algorithms

The default model preset is `balanced_gbdt`.

Training pipeline:

```text
StandardScaler
HistGradientBoostingClassifier
```

Default classifier settings:

```text
learning_rate: 0.035
max_iter: 450
max_leaf_nodes: 10
min_samples_leaf: 80
l2_regularization: 0.3
random_state: 42
```

Other available model presets:

- `fast_gbdt`: smaller/faster `HistGradientBoostingClassifier`.
- `random_forest`: `RandomForestClassifier` with 350 trees, sqrt feature
  sampling, balanced subsample class weights, and parallel fitting.

The label is binary:

```text
1 if stock future 5-trading-day return > SPY future 5-trading-day return
0 otherwise
```

Feature groups:

- Momentum returns: 1, 5, 10, 20, 60, and 120 trading days.
- Volatility: 10, 20, and 60 trading days.
- Volume and dollar-volume z-scores.
- Moving-average ratios: 10, 20, 50, and 100 trading days.
- 60-day drawdown.
- Daily high-low range.
- 14-day RSI.
- SPY-relative returns and SPY volatility.
- Cross-sectional ranks for momentum, relative strength, low volatility,
  moving-average ratio, drawdown, and RSI.

Default scoring mode for paper testing is `model_momentum_blend`:

```text
score = 25% model probability rank + 75% 20-day momentum rank
```

Available scoring modes:

- `model`: rank by model probability only.
- `model_momentum_blend`: rank by the blended formula above.
- `momentum_20d`: rank by 20-day momentum only.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

To connect Alpaca paper trading, create `.env` with paper credentials:

```text
ALPACA_API_KEY=your_paper_key
ALPACA_SECRET_KEY=your_paper_secret
ALPACA_PAPER=true
```

If credentials are present, the paper-trade script reads live Alpaca paper
account equity and positions even in dry-run mode. Without credentials, it uses
`--dry-run-equity`, which defaults to `$100,000`.

## Weekly Reweight Runbook

Run this every 5 trading days, preferably after the latest market data is
available.

```bash
.venv/bin/python scripts/download_yahoo_data.py
.venv/bin/python scripts/train_model.py
.venv/bin/python scripts/generate_recommendations.py --strategy model_momentum_blend --top-n 5
.venv/bin/python scripts/generate_recommendations.py --strategy momentum_20d --top-n 5 --out reports/latest_momentum_recommendations.csv
.venv/bin/python scripts/alpaca_paper_trade.py --recommendations reports/latest_recommendations.csv
```

The last command is a dry run. It writes `reports/latest_order_plan.json` and
appends to `reports/paper_trading_log.jsonl`.

After reviewing the dry-run plan, submit paper orders with:

```bash
.venv/bin/python scripts/alpaca_paper_trade.py --recommendations reports/latest_recommendations.csv --submit
```

The submit command sends market orders to Alpaca paper trading and appends the
submitted order ids to the latest audit record.

## Submit Checklist

Before running the `--submit` command, verify:

- `reports/latest_recommendations.csv` has today's intended market date.
- `reports/latest_order_plan.json` has `"mode": "dry_run"`.
- `market_regime.status` is `"ok"`.
- `market_regime.risk_off` is understood and the adjusted exposure is expected.
- `excluded_recommendations` is empty or every exclusion is intentional.
- `adjusted_targets` match the intended names and weights.
- The generated sells and buys are expected, especially full exits.
- Paper account equity and positions look right.
- There are no stale recommendations from a prior reweight.
- You are using Alpaca paper credentials, not live credentials.

## Custom Run Options

Download a smaller starter universe:

```bash
.venv/bin/python scripts/download_yahoo_data.py --universe starter
```

Download explicit symbols:

```bash
.venv/bin/python scripts/download_yahoo_data.py --symbols AAPL MSFT NVDA SPY
```

Train a different model preset or scoring mode:

```bash
.venv/bin/python scripts/train_model.py --model-preset random_forest --score-mode model
```

Change the blend weight:

```bash
.venv/bin/python scripts/train_model.py --model-weight 0.35
.venv/bin/python scripts/generate_recommendations.py --strategy model_momentum_blend --model-weight 0.35
```

Tune paper-trading risk controls:

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

## Validation And Tests

Run these after changing code, changing strategy settings, or before relying on a
new reweight:

```bash
.venv/bin/python -m compileall src scripts
.venv/bin/python scripts/predict_latest.py
.venv/bin/python scripts/walk_forward.py
.venv/bin/python scripts/baselines.py
```

`walk_forward.py` trains on prior years and tests one year at a time.
`baselines.py` compares against non-ML strategies:

- `momentum_20d`
- `low_volatility_20d`
- `relative_strength_20d`

There is currently no dedicated `tests/` suite; these scripts are the main smoke
and validation checks.

## Where To Track Results

Operational files:

```text
reports/latest_recommendations.csv          latest traded model/momentum blend picks
reports/latest_momentum_recommendations.csv latest plain momentum comparison picks
reports/latest_order_plan.json              latest dry-run or submit order plan
reports/paper_trading_log.jsonl             append-only dry-run/submit audit log
```

Model and backtest files:

```text
models/stock_ranker.joblib                  trained model artifact and metadata
reports/metrics.json                        latest train/test metrics and standard backtest
reports/equity_curve.csv                    standard backtest equity curve
reports/trades.csv                          standard backtest trades
reports/model_frame.csv                     labeled feature frame used for training/backtest
reports/test_scores.csv                     model scores on the test period
```

Validation files:

```text
reports/walk_forward_metrics.json           walk-forward aggregate and fold metrics
reports/walk_forward_summary.csv            walk-forward fold table
reports/walk_forward_equity_*.csv           yearly walk-forward equity curves
reports/walk_forward_trades_*.csv           yearly walk-forward trades
reports/baseline_metrics.json               baseline strategy metrics
reports/baseline_*_equity.csv               baseline equity curves
reports/baseline_*_trades.csv               baseline trades
```

Market data files:

```text
data/raw/yahoo/all_symbols.csv              combined Yahoo OHLCV data
data/raw/yahoo/{SYMBOL}.csv                 per-symbol Yahoo OHLCV data
```

Useful fields in `reports/latest_order_plan.json`:

- `created_at`: when the plan was created.
- `mode`: `dry_run` or `submit`.
- `equity`: Alpaca paper account equity or configured dry-run equity.
- `market_regime`: SPY risk filter state.
- `original_targets`: raw recommendation weights.
- `eligible_targets`: recommendations that passed price/volatility filters.
- `adjusted_targets`: final target weights after caps and risk controls.
- `excluded_recommendations`: picks rejected by risk controls.
- `positions`: current Alpaca paper positions used for the plan.
- `orders`: proposed orders.
- `submitted_orders`: Alpaca order ids, only present on submit runs.

## Troubleshooting

- Yahoo data refresh requires network access.
- Alpaca dry-run with `.env` credentials requires network access because it reads
  current paper account state.
- If recommendations are stale, rerun the full weekly runbook from data download.
- If `reports/latest_order_plan.json` has an old `created_at`, regenerate the
  dry-run plan before submitting.
- If `market_regime.status` is not `"ok"`, inspect `data/raw/yahoo/all_symbols.csv`
  and make sure `SPY` has enough recent rows.
- If `excluded_recommendations` is not empty, check whether the stock failed the
  minimum price or maximum volatility guardrail.
- If dependencies are missing, rerun `.venv/bin/python -m pip install -r requirements.txt`.
