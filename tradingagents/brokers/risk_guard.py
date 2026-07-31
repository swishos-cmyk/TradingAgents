# TradingAgents/brokers/risk_guard.py
"""Deterministic risk engine for the daily trading graph.

The LLM agents propose trades; this module has the final word. It is
plain Python on purpose — no prompt, however persuasive, can talk it into
oversizing a position or trading through a halt.

Circuit breakers enforced:
  * per-trade risk cap (distance to stop x size vs. equity)
  * per-position notional cap
  * minimum reward:risk on every plan
  * daily loss halt — no new entries after the day's drawdown limit
  * kill switch — drawdown from the high-water mark pauses the strategy
    until a human resets it
  * pattern-day-trader tracking for margin accounts under $25k
  * settled-funds check for cash accounts

State (high-water mark, day-trade log, halt flags) persists to JSON so a
scheduled daily run keeps one continuous risk ledger.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional


DEFAULT_RISK_PARAMS = {
    "max_risk_per_trade_pct": 0.02,     # max % of equity lost if the stop is hit
    "max_position_pct": 0.50,           # max % of equity in a single position
    "max_open_positions": 2,
    "min_reward_risk": 2.0,             # target must be >= 2x the stop distance
    "daily_loss_halt_pct": 0.05,        # stop opening new trades after -5% on the day
    "kill_switch_drawdown_pct": 0.20,   # pause strategy at -20% from high-water mark
    "min_price": 1.00,                  # avoid sub-$1 stocks (spreads, halts)
    "max_price": 400.00,
    "pdt_max_day_trades": 3,            # margin accounts under $25k: 3 per 5 sessions
    "pdt_equity_threshold": 25_000.0,
}


@dataclass
class RiskVerdict:
    approved: bool
    quantity: float = 0.0
    notional: float = 0.0
    reasons: List[str] = field(default_factory=list)
    halted: bool = False

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "quantity": self.quantity,
            "notional": self.notional,
            "reasons": self.reasons,
            "halted": self.halted,
        }


class RiskGuard:
    def __init__(self, state_path: str, params: Optional[Dict] = None):
        self.state_path = state_path
        self.params = {**DEFAULT_RISK_PARAMS, **(params or {})}
        self._state = self._load_state()

    # ------------------------------------------------------------------
    def _load_state(self) -> Dict:
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "high_water_mark": 0.0,
            "day_start_equity": {},   # date -> equity at first check that day
            "day_trades": [],         # [{"date": iso, "symbol": str}]
            "kill_switch_engaged": False,
            "kill_switch_reason": "",
        }

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    # ------------------------------------------------------------------
    def record_day_trade(self, symbol: str, on_date: Optional[str] = None) -> None:
        self._state["day_trades"].append(
            {"date": on_date or date.today().isoformat(), "symbol": symbol}
        )
        self._save_state()

    def day_trades_in_window(self, on_date: Optional[str] = None, window: int = 5) -> int:
        anchor = date.fromisoformat(on_date) if on_date else date.today()
        cutoff = anchor - timedelta(days=window + 2)  # rough 5-business-day window
        return sum(
            1
            for t in self._state["day_trades"]
            if date.fromisoformat(t["date"]) > cutoff
        )

    def reset_kill_switch(self) -> None:
        """Manual restart after a kill-switch halt. Re-anchors the
        high-water mark to the next observed equity so the same (already
        acknowledged) drawdown doesn't instantly re-trip the switch."""
        self._state["kill_switch_engaged"] = False
        self._state["kill_switch_reason"] = ""
        self._state["high_water_mark"] = 0.0
        self._save_state()

    # ------------------------------------------------------------------
    def check_account_health(self, equity: float, on_date: Optional[str] = None) -> List[str]:
        """Update equity ledgers and return any active halt reasons."""
        today = on_date or date.today().isoformat()
        halts = []

        if equity > self._state["high_water_mark"]:
            self._state["high_water_mark"] = equity

        if today not in self._state["day_start_equity"]:
            self._state["day_start_equity"][today] = equity

        hwm = self._state["high_water_mark"]
        if hwm > 0:
            drawdown = (hwm - equity) / hwm
            if drawdown >= self.params["kill_switch_drawdown_pct"]:
                self._state["kill_switch_engaged"] = True
                self._state["kill_switch_reason"] = (
                    f"Drawdown {drawdown:.1%} from high-water mark ${hwm:.2f} "
                    f"breached the {self.params['kill_switch_drawdown_pct']:.0%} kill switch."
                )

        if self._state["kill_switch_engaged"]:
            halts.append(
                "KILL SWITCH ENGAGED: " + self._state["kill_switch_reason"]
                + " Manual reset required (RiskGuard.reset_kill_switch)."
            )

        day_start = self._state["day_start_equity"][today]
        if day_start > 0:
            day_loss = (day_start - equity) / day_start
            if day_loss >= self.params["daily_loss_halt_pct"]:
                halts.append(
                    f"DAILY LOSS HALT: down {day_loss:.1%} today "
                    f"(limit {self.params['daily_loss_halt_pct']:.0%}). No new entries."
                )

        self._save_state()
        return halts

    # ------------------------------------------------------------------
    def size_position(self, equity: float, entry: float, stop: float) -> Dict:
        """Risk-based position sizing: risk budget / stop distance, capped
        by the per-position notional limit."""
        stop_distance = entry - stop
        if stop_distance <= 0:
            return {"quantity": 0.0, "notional": 0.0, "risk_amount": 0.0}
        risk_budget = equity * self.params["max_risk_per_trade_pct"]
        qty_by_risk = risk_budget / stop_distance
        max_notional = equity * self.params["max_position_pct"]
        qty_by_notional = max_notional / entry
        quantity = round(min(qty_by_risk, qty_by_notional), 4)
        return {
            "quantity": quantity,
            "notional": round(quantity * entry, 2),
            "risk_amount": round(quantity * stop_distance, 2),
        }

    # ------------------------------------------------------------------
    def evaluate(
        self,
        plan: Dict,
        account: Dict,
        open_positions: int = 0,
        is_day_trade: bool = False,
        on_date: Optional[str] = None,
    ) -> RiskVerdict:
        """Validate a trade plan against every circuit breaker.

        plan requires: symbol, entry_price, stop_loss, take_profit.
        account requires: equity, settled_cash, account_type.
        """
        reasons = []
        equity = float(account.get("equity", 0))

        halts = self.check_account_health(equity, on_date=on_date)
        if halts:
            return RiskVerdict(approved=False, reasons=halts, halted=True)

        entry = float(plan.get("entry_price") or 0)
        stop = float(plan.get("stop_loss") or 0)
        target = float(plan.get("take_profit") or 0)

        if entry <= 0 or stop <= 0:
            return RiskVerdict(approved=False, reasons=["Plan missing entry_price or stop_loss."])
        if stop >= entry:
            return RiskVerdict(
                approved=False,
                reasons=[f"Stop {stop} must be below entry {entry} for a long trade."],
            )
        if entry < self.params["min_price"] or entry > self.params["max_price"]:
            return RiskVerdict(
                approved=False,
                reasons=[
                    f"Entry ${entry:.2f} outside allowed price band "
                    f"[${self.params['min_price']}, ${self.params['max_price']}]."
                ],
            )

        if target > 0:
            reward_risk = (target - entry) / (entry - stop)
            if reward_risk < self.params["min_reward_risk"]:
                return RiskVerdict(
                    approved=False,
                    reasons=[
                        f"Reward:risk {reward_risk:.2f} below required "
                        f"{self.params['min_reward_risk']:.1f}. Skip mediocre setups."
                    ],
                )
        else:
            return RiskVerdict(approved=False, reasons=["Plan missing take_profit."])

        if open_positions >= self.params["max_open_positions"]:
            return RiskVerdict(
                approved=False,
                reasons=[
                    f"Already at max open positions ({self.params['max_open_positions']})."
                ],
            )

        # PDT: margin accounts under the equity threshold get 3 day trades / 5 sessions
        if (
            is_day_trade
            and account.get("account_type") == "margin"
            and equity < self.params["pdt_equity_threshold"]
        ):
            used = self.day_trades_in_window(on_date=on_date)
            if used >= self.params["pdt_max_day_trades"]:
                return RiskVerdict(
                    approved=False,
                    reasons=[
                        f"PDT limit: {used} day trades already used in the rolling "
                        "5-session window on a sub-$25k margin account."
                    ],
                )

        sizing = self.size_position(equity, entry, stop)
        if sizing["quantity"] <= 0:
            return RiskVerdict(approved=False, reasons=["Position sizing produced zero quantity."])

        # cash accounts can only spend settled funds
        settled = float(account.get("settled_cash", account.get("cash", 0)))
        if account.get("account_type", "cash") == "cash" and sizing["notional"] > settled:
            if settled < self.params["min_price"]:
                return RiskVerdict(
                    approved=False,
                    reasons=[f"Settled cash ${settled:.2f} too small to trade."],
                )
            adj_qty = round((settled * 0.98) / entry, 4)
            sizing = {
                "quantity": adj_qty,
                "notional": round(adj_qty * entry, 2),
                "risk_amount": round(adj_qty * (entry - stop), 2),
            }
            reasons.append(
                f"Size reduced to fit settled cash (${settled:.2f})."
            )

        reasons.append(
            f"Approved: {sizing['quantity']} shares (~${sizing['notional']:.2f}), "
            f"risking ${sizing['risk_amount']:.2f} "
            f"({sizing['risk_amount'] / equity:.2%} of equity) to the stop."
        )
        return RiskVerdict(
            approved=True,
            quantity=sizing["quantity"],
            notional=sizing["notional"],
            reasons=reasons,
        )
