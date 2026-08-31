"""
CLI entrypoint for the Alpaca AI Trading Agent.

Examples:
    python main.py run                          # one cycle over the default watchlist, dry-run off
    python main.py run --dry-run                 # decide but don't place real paper orders
    python main.py run --symbols AAPL,MSFT        # override the watchlist
    python main.py loop --interval 300            # run continuously every 5 minutes
    python main.py status                          # print account + open positions
"""

from __future__ import annotations

import argparse
import os
import time

from dotenv import load_dotenv

from src.alpaca_client import AlpacaClient
from src.trading_agent import TradingAgent

load_dotenv()


def get_watchlist(cli_symbols: str | None) -> list[str]:
    if cli_symbols:
        return [s.strip().upper() for s in cli_symbols.split(",") if s.strip()]
    env_list = os.getenv("WATCHLIST", "AAPL,MSFT,TSLA,NVDA,SPY")
    return [s.strip().upper() for s in env_list.split(",") if s.strip()]


def cmd_status(_args) -> None:
    client = AlpacaClient()
    account = client.get_account()
    positions = client.get_positions()

    print("\n=== Account ===")
    print(f"Equity:        ${account.equity:,.2f}")
    print(f"Cash:          ${account.cash:,.2f}")
    print(f"Buying power:  ${account.buying_power:,.2f}")
    print(f"Daily P&L:     {account.daily_pnl_pct:.2%}")

    print("\n=== Open Positions ===")
    if not positions:
        print("(none)")
    for p in positions:
        print(f"{p['symbol']:>6}  qty={p['qty']:<10}  entry=${p['avg_entry_price']:.2f}  "
              f"now=${p['current_price']:.2f}  P&L={p['unrealized_plpc']:.2%}")
    print()


def cmd_run(args) -> None:
    watchlist = get_watchlist(args.symbols)
    print(f"Running one agent cycle over: {watchlist}  (dry_run={args.dry_run})\n")

    agent = TradingAgent(dry_run=args.dry_run)
    results = agent.run_cycle(watchlist)

    for r in results:
        print(f"\n--- {r['symbol']} ---")
        if r["status"] != "ok":
            print(f"  {r['status']}: {r.get('reason')}")
            continue
        print(f"  LLM decision : {r['llm_action'].upper()} (confidence {r['confidence']:.2f})")
        print(f"  Reasoning    : {r['reasoning']}")
        print(f"  Risk note    : {r['risk_note']}")
        print(f"  Risk verdict : {'APPROVED' if r['risk_approved'] else 'REJECTED'} - {r['risk_reason']}")
        if r["order"]:
            print(f"  Order        : {r['order']}")


def cmd_loop(args) -> None:
    watchlist = get_watchlist(args.symbols)
    print(f"Starting continuous loop every {args.interval}s over: {watchlist}")
    print("Press Ctrl+C to stop.\n")

    agent = TradingAgent(dry_run=args.dry_run)
    try:
        while True:
            agent.run_cycle(watchlist)
            print(f"Cycle complete. Sleeping {args.interval}s...")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpaca AI Trading Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_status = subparsers.add_parser("status", help="Show account + positions")
    p_status.set_defaults(func=cmd_status)

    p_run = subparsers.add_parser("run", help="Run a single agent cycle")
    p_run.add_argument("--symbols", type=str, default=None, help="Comma-separated tickers")
    p_run.add_argument("--dry-run", action="store_true", help="Decide but do not place orders")
    p_run.set_defaults(func=cmd_run)

    p_loop = subparsers.add_parser("loop", help="Run continuously")
    p_loop.add_argument("--symbols", type=str, default=None)
    p_loop.add_argument("--interval", type=int, default=300, help="Seconds between cycles")
    p_loop.add_argument("--dry-run", action="store_true")
    p_loop.set_defaults(func=cmd_loop)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
