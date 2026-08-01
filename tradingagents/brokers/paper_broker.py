# TradingAgents/brokers/paper_broker.py
"""File-backed paper broker.

Simulates a small cash account with immediate fills at a reference price
plus configurable slippage, and T+1 settlement so cash-account strategies
face the same settled-funds constraint they would on Robinhood.

State is persisted as JSON so consecutive daily runs (e.g. a cron job)
share one continuous account.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from .base_broker import Account, BaseBroker, Order, Position


def _default_quote_fn(symbol: str) -> float:
    """Fetch the latest price via yfinance. Injectable for tests."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    price = None
    try:
        price = ticker.fast_info.last_price
    except Exception:
        pass
    if not price:
        hist = ticker.history(period="1d")
        if len(hist) > 0:
            price = float(hist["Close"].iloc[-1])
    if not price:
        raise ValueError(f"Could not fetch quote for {symbol}")
    return float(price)


class PaperBroker(BaseBroker):
    name = "paper"
    is_live = False

    def __init__(
        self,
        state_path: str,
        starting_cash: float = 1000.0,
        slippage_bps: float = 10.0,
        settlement_days: int = 1,
        quote_fn: Optional[Callable[[str], float]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self.state_path = state_path
        self.starting_cash = starting_cash
        self.slippage_bps = slippage_bps
        self.settlement_days = settlement_days
        self.quote_fn = quote_fn or _default_quote_fn
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._state = self._load_state()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> Dict:
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "cash": self.starting_cash,
            "pending_settlements": [],  # [{"amount": float, "settles_at": iso}]
            "positions": {},  # symbol -> {"quantity": float, "avg_price": float}
            "orders": [],
            "stop_orders": [],  # open protective stops
        }

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2)

    # ------------------------------------------------------------------
    # settlement
    # ------------------------------------------------------------------
    def _apply_settlements(self) -> None:
        now = self.now_fn()
        remaining = []
        for pending in self._state["pending_settlements"]:
            if datetime.fromisoformat(pending["settles_at"]) <= now:
                self._state["cash"] += pending["amount"]
            else:
                remaining.append(pending)
        self._state["pending_settlements"] = remaining

    def _unsettled_total(self) -> float:
        return sum(p["amount"] for p in self._state["pending_settlements"])

    # ------------------------------------------------------------------
    # broker surface
    # ------------------------------------------------------------------
    def get_account(self) -> Account:
        self._apply_settlements()
        positions_value = sum(p.market_value for p in self.get_positions())
        settled = self._state["cash"]
        unsettled = self._unsettled_total()
        return Account(
            equity=round(settled + unsettled + positions_value, 2),
            cash=round(settled + unsettled, 2),
            settled_cash=round(settled, 2),
            buying_power=round(settled, 2),
            account_type="cash",
        )

    def get_positions(self) -> List[Position]:
        positions = []
        for symbol, pos in self._state["positions"].items():
            if pos["quantity"] <= 0:
                continue
            try:
                last = self.quote_fn(symbol)
            except Exception:
                last = pos["avg_price"]
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=pos["quantity"],
                    avg_price=pos["avg_price"],
                    last_price=last,
                )
            )
        return positions

    def get_quote(self, symbol: str) -> float:
        return self.quote_fn(symbol)

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: str = "gfd",
        extended_hours: bool = False,
    ) -> Order:
        self._apply_settlements()
        reference = self.get_quote(symbol)
        slip = reference * (self.slippage_bps / 10_000.0)

        # Honor limit semantics: a limit order only fills when the market
        # is at or through the limit, and never at a worse price than it.
        if order_type == "limit" and limit_price is not None:
            marketable = reference <= limit_price if side == "buy" else reference >= limit_price
            if not marketable:
                order = Order(
                    order_id=str(uuid.uuid4())[:8],
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    order_type=order_type,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    time_in_force=time_in_force,
                    status="unfilled",
                )
                self._state["orders"].append(order.to_dict())
                self._save_state()
                return order
            if side == "buy":
                fill_price = round(min(reference + slip, limit_price), 4)
            else:
                fill_price = round(max(reference - slip, limit_price), 4)
        else:
            fill_price = round(reference + slip, 4) if side == "buy" else round(reference - slip, 4)

        cost = fill_price * quantity
        if side == "buy":
            if cost > self._state["cash"] + 1e-9:
                raise ValueError(
                    f"Insufficient settled cash: need ${cost:.2f}, have ${self._state['cash']:.2f}"
                )
            self._state["cash"] -= cost
            pos = self._state["positions"].get(symbol, {"quantity": 0.0, "avg_price": 0.0})
            total_qty = pos["quantity"] + quantity
            pos["avg_price"] = (pos["avg_price"] * pos["quantity"] + cost) / total_qty
            pos["quantity"] = total_qty
            self._state["positions"][symbol] = pos
        elif side == "sell":
            pos = self._state["positions"].get(symbol)
            if not pos or pos["quantity"] + 1e-9 < quantity:
                raise ValueError(f"Cannot sell {quantity} {symbol}: position too small")
            pos["quantity"] = round(pos["quantity"] - quantity, 8)
            if pos["quantity"] <= 1e-8:
                del self._state["positions"][symbol]
            # proceeds settle T+N in a cash account
            settles_at = self.now_fn() + timedelta(days=self.settlement_days)
            self._state["pending_settlements"].append(
                {"amount": cost, "settles_at": settles_at.isoformat()}
            )
        else:
            raise ValueError(f"Unknown side: {side}")

        order = Order(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            status="filled",
            filled_price=fill_price,
            filled_at=self.now_fn().isoformat(),
        )
        self._state["orders"].append(order.to_dict())
        self._save_state()
        return order

    def place_stop_loss(self, symbol: str, quantity: float, stop_price: float) -> Order:
        order = Order(
            order_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            side="sell",
            quantity=quantity,
            order_type="stop",
            stop_price=stop_price,
            time_in_force="gtc",
            status="open",
        )
        self._state["stop_orders"].append(order.to_dict())
        self._save_state()
        return order

    def get_order(self, order_id: str) -> Optional[Order]:
        for record in self._state["orders"]:
            if record.get("order_id") == order_id:
                return Order(**record)
        return None

    def cancel_open_orders(self, symbol: Optional[str] = None) -> int:
        before = len(self._state["stop_orders"])
        if symbol:
            self._state["stop_orders"] = [
                o for o in self._state["stop_orders"] if o["symbol"] != symbol
            ]
        else:
            self._state["stop_orders"] = []
        self._save_state()
        return before - len(self._state["stop_orders"])

    # ------------------------------------------------------------------
    # paper-only helper: simulate stop-loss triggers on latest quotes
    # ------------------------------------------------------------------
    def process_stops(self) -> List[Order]:
        """Check open protective stops against current quotes and fill any
        that are breached. Call once per run before making new decisions."""
        filled = []
        remaining = []
        for stop in self._state["stop_orders"]:
            try:
                last = self.get_quote(stop["symbol"])
            except Exception:
                remaining.append(stop)
                continue
            held = self._state["positions"].get(stop["symbol"], {}).get("quantity", 0)
            if last <= stop["stop_price"] and held >= stop["quantity"] - 1e-9:
                filled.append(
                    self.place_order(
                        stop["symbol"], "sell", stop["quantity"], order_type="market"
                    )
                )
            else:
                remaining.append(stop)
        self._state["stop_orders"] = remaining
        self._save_state()
        return filled
