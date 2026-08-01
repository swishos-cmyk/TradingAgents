#!/usr/bin/env python3
"""Entry point for the Claude-powered daily trading desk.

Usage:
    # morning session (paper broker by default)
    python run_daily.py --date 2026-08-03

    # evening reflection: strategy lab updates the playbook
    python run_daily.py --date 2026-08-03 --reflect --pnl-note "TSLA +1.2R"

    # live Robinhood execution (read docs/DAILY_TRADING.md first!)
    export RH_USERNAME=... RH_PASSWORD=... RH_MFA_SECRET=...
    export TRADINGAGENTS_LIVE_TRADING=I_UNDERSTAND_THE_RISKS
    python run_daily.py --date 2026-08-03 --mode live

Schedule with cron (times in ET; adjust for your TZ):
    45 9  * * 1-5  cd /path/to/TradingAgents && python run_daily.py
    30 16 * * 1-5  cd /path/to/TradingAgents && python run_daily.py --reflect
"""

import argparse
import json
import sys
from datetime import date


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily trading desk")
    parser.add_argument(
        "--date", default=date.today().isoformat(), help="session date yyyy-mm-dd"
    )
    parser.add_argument(
        "--mode", choices=["paper", "live"], default=None,
        help="override execution mode (default: config value, i.e. paper)",
    )
    parser.add_argument(
        "--reflect", action="store_true",
        help="run the evening strategy-lab reflection instead of a trading session",
    )
    parser.add_argument(
        "--pnl-note", default="",
        help="free-form realized P&L note passed to the strategy lab",
    )
    parser.add_argument("--debug", action="store_true", help="stream agent messages")
    args = parser.parse_args()

    from tradingagents.graph.daily_trading_graph import DailyTradingGraph

    config_overrides = {}
    if args.mode:
        config_overrides["execution_mode"] = args.mode

    desk = DailyTradingGraph(config=config_overrides, debug=args.debug)

    if args.reflect:
        print(f"=== Strategy Lab reflection for {args.date} ===")
        updated = desk.reflect(args.date, realized_pnl_note=args.pnl_note)
        print("Updated playbook:")
        print(updated)
        return 0

    print(f"=== Daily trading session {args.date} "
          f"({desk.broker.name} broker) ===")
    final_state = desk.run_session(args.date)

    print("\n--- Selected ticker ---")
    print(final_state.get("selected_ticker") or "NO_TRADE")
    print("\n--- Trade plan ---")
    print(final_state.get("trade_plan", ""))
    print("\n--- Risk verdict ---")
    print(final_state.get("risk_verdict", ""))
    print("\n--- Execution report ---")
    print(final_state.get("execution_report", ""))

    try:
        verdict = json.loads(final_state.get("risk_verdict", "{}"))
        if verdict.get("halted"):
            print("\n*** TRADING HALTED BY RISK GUARD — see reasons above ***")
    except json.JSONDecodeError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
