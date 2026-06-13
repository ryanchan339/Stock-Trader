"""Intraday data layer: confirm the Alpaca market-data feed works.

Two modes, neither of which trades:

  snapshot  Fetch recent 1-minute bars for the universe via REST and write them
            to a CSV. Good for a quick "is the feed alive?" check and for
            building intraday backtests later.

  stream    Open the Alpaca websocket and append live 1-minute bars to a CSV as
            they arrive, until interrupted. Good for confirming the realtime
            connection the live trader will depend on.

Examples:
  .venv/bin/python scripts/stream_intraday.py snapshot --universe starter
  .venv/bin/python scripts/stream_intraday.py stream --symbols AAPL MSFT NVDA
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_trader.env import load_dotenv
from stock_trader.intraday import (
    build_data_client,
    credentials_from_env,
    fetch_minute_bars,
)
from stock_trader.universes import get_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream or snapshot Alpaca intraday bars.")
    parser.add_argument("mode", choices=["snapshot", "stream"])
    parser.add_argument("--universe", default="starter")
    parser.add_argument("--symbols", nargs="*", help="Explicit symbols; overrides --universe.")
    parser.add_argument("--lookback-minutes", type=int, default=120)
    parser.add_argument("--feed", default="iex", choices=["iex", "sip"])
    parser.add_argument("--out", default="data/intraday/minute_bars.csv")
    return parser.parse_args()


def resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [symbol.upper() for symbol in args.symbols]
    return get_universe(args.universe)


def run_snapshot(args: argparse.Namespace, symbols: list[str]) -> None:
    creds = credentials_from_env()
    if not creds.has_keys:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for market data.")
    data_client = build_data_client(creds)
    bars = fetch_minute_bars(
        data_client, symbols, lookback_minutes=args.lookback_minutes, feed=args.feed
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bars.to_csv(out_path, index=False)
    print(f"Wrote {len(bars)} bars for {bars['symbol'].nunique()} symbols to {out_path}")
    if not bars.empty:
        latest = bars.sort_values("timestamp").groupby("symbol").tail(1)
        for row in latest.itertuples(index=False):
            print(f"  {row.symbol:6} {row.timestamp}  close={row.close}  vwap={row.vwap}")


def run_stream(args: argparse.Namespace, symbols: list[str]) -> None:
    creds = credentials_from_env()
    if not creds.has_keys:
        raise SystemExit("ALPACA_API_KEY and ALPACA_SECRET_KEY are required for market data.")
    try:
        from alpaca.data.live import StockDataStream
    except ImportError as exc:
        raise SystemExit(
            "alpaca-py is not installed. Run: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out_path.exists()
    handle = out_path.open("a", newline="", encoding="utf-8")
    writer = csv.writer(handle)
    if new_file:
        writer.writerow(["received_at", "symbol", "timestamp", "open", "high", "low", "close", "volume", "vwap"])
        handle.flush()

    stream = StockDataStream(creds.api_key, creds.secret_key, feed=args.feed)

    async def on_bar(bar) -> None:
        writer.writerow(
            [
                datetime.now(UTC).isoformat(),
                bar.symbol,
                bar.timestamp,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                getattr(bar, "vwap", ""),
            ]
        )
        handle.flush()
        print(f"{bar.symbol:6} {bar.timestamp}  close={bar.close}  vol={bar.volume}", flush=True)

    stream.subscribe_bars(on_bar, *symbols)
    print(f"Streaming {len(symbols)} symbols to {out_path}. Ctrl-C to stop.")
    try:
        stream.run()
    except KeyboardInterrupt:
        print("\nStopping stream.")
    finally:
        handle.close()


def main() -> None:
    args = parse_args()
    load_dotenv()
    symbols = resolve_symbols(args)
    if args.mode == "snapshot":
        run_snapshot(args, symbols)
    else:
        run_stream(args, symbols)


if __name__ == "__main__":
    main()
