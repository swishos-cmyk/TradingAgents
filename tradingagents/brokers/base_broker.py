# TradingAgents/brokers/base_broker.py
"""Abstract broker interface used by the daily trading graph.

Every broker (paper or live) exposes the same small surface so the
execution agent and risk guard never need to know which venue they are
talking to.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class Account:
    equity: float
    cash: float
    settled_cash: float
    buying_power: float
    account_type: str = "cash"  # "cash" or "margin"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_price: float
    last_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.last_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.last_price - self.avg_price) * self.quantity

    def to_dict(self) -> dict:
        d = asdict(self)
        d["market_value"] = self.market_value
        d["unrealized_pnl"] = self.unrealized_pnl
        return d


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    quantity: float
    order_type: str  # "market" | "limit"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "gfd"
    status: str = "filled"  # paper broker fills immediately
    filled_price: Optional[float] = None
    filled_at: Optional[str] = None
    meta: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class BaseBroker(ABC):
    """Minimal broker surface required by the daily trading graph."""

    name: str = "base"
    is_live: bool = False

    @abstractmethod
    def get_account(self) -> Account:
        """Return the current account snapshot."""

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Return all open positions."""

    @abstractmethod
    def get_quote(self, symbol: str) -> float:
        """Return the latest trade price for a symbol."""

    @abstractmethod
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
        """Place an order and return the (possibly pending) order record."""

    @abstractmethod
    def place_stop_loss(self, symbol: str, quantity: float, stop_price: float) -> Order:
        """Attach a stop-loss sell order to an existing long position."""

    @abstractmethod
    def cancel_open_orders(self, symbol: Optional[str] = None) -> int:
        """Cancel open orders (optionally for one symbol). Returns count."""
