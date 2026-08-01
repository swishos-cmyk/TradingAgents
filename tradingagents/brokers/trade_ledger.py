# TradingAgents/brokers/trade_ledger.py
"""Open-trade ledger.

Every executed entry is recorded here with its plan (stop, target, max
holding period). The morning position-management sweep reads it back to
enforce target and time exits, and to tell whether an exit closed a
position opened the same session (a day trade for PDT accounting).
"""

import json
import os
from typing import Dict, List, Optional


def load_ledger(path: str) -> List[Dict]:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_ledger(path: str, trades: List[Dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)


def open_trade(
    path: str,
    symbol: str,
    quantity: float,
    entry_date: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    max_holding_days: int,
) -> Dict:
    trades = load_ledger(path)
    trade = {
        "symbol": symbol,
        "quantity": quantity,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "max_holding_days": max_holding_days,
        "status": "open",
        "exit_date": None,
        "exit_price": None,
        "exit_reason": None,
    }
    trades.append(trade)
    save_ledger(path, trades)
    return trade


def open_trades(path: str) -> List[Dict]:
    return [t for t in load_ledger(path) if t.get("status") == "open"]


def close_trade(
    path: str,
    symbol: str,
    exit_date: str,
    exit_price: Optional[float],
    reason: str,
) -> Optional[Dict]:
    """Mark the oldest open trade in `symbol` closed. Returns the closed
    entry (with entry_date preserved) or None if nothing was open."""
    trades = load_ledger(path)
    for trade in trades:
        if trade.get("status") == "open" and trade.get("symbol") == symbol:
            trade["status"] = "closed"
            trade["exit_date"] = exit_date
            trade["exit_price"] = exit_price
            trade["exit_reason"] = reason
            save_ledger(path, trades)
            return trade
    return None
