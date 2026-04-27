from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.universes import UNIVERSES, get_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download adjusted historical OHLCV data from Yahoo Finance."
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="Ticker symbols to download. Overrides --universe.",
    )
    parser.add_argument(
        "--universe",
        default="large_cap",
        choices=sorted(UNIVERSES),
        help="Named ticker universe to download when --symbols is not provided.",
    )
    parser.add_argument("--start", default="2018-01-01", help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", default=None, help="End date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument(
        "--out-dir",
        default="data/raw/yahoo",
        help="Directory where CSV files will be written.",
    )
    return parser.parse_args()


def normalize_download(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.droplevel(1, axis=1)

    frame = frame.reset_index()
    frame.insert(0, "symbol", symbol)
    frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
    return frame


def main() -> None:
    args = parse_args()
    symbols = args.symbols or get_universe(args.universe)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for symbol in symbols:
        print(f"Downloading {symbol}...")
        data = yf.download(
            symbol,
            start=args.start,
            end=args.end,
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        if data.empty:
            print(f"  No rows returned for {symbol}; skipping.")
            continue

        normalized = normalize_download(data, symbol)
        symbol_path = out_dir / f"{symbol}.csv"
        normalized.to_csv(symbol_path, index=False)
        rows.append(normalized)
        print(f"  Wrote {len(normalized):,} rows to {symbol_path}")

    if not rows:
        raise SystemExit("No data downloaded.")

    combined = pd.concat(rows, ignore_index=True)
    combined_path = out_dir / "all_symbols.csv"
    combined.to_csv(combined_path, index=False)
    print(f"Wrote {len(combined):,} combined rows to {combined_path}")


if __name__ == "__main__":
    main()
