# TradingAgents/brokers/robinhood_broker.py
"""Robinhood connector built on the community `robin_stocks` library.

IMPORTANT
---------
* Robinhood has no official public trading API. `robin_stocks` drives the
  same endpoints the app uses; Robinhood may throttle, break, or restrict
  accounts using it. Use at your own risk and re-read Robinhood's terms.
* Live trading is double-gated: the config must set
  ``execution_mode: "live"`` AND the environment must contain
  ``TRADINGAGENTS_LIVE_TRADING=I_UNDERSTAND_THE_RISKS``. Without both the
  constructor raises, so a mis-configured cron job can never fire real
  orders by accident.

Credentials come from the environment:
    RH_USERNAME, RH_PASSWORD, and optionally RH_MFA_SECRET (the TOTP
    seed shown when enabling two-factor auth; requires `pyotp`).

Dependencies (only needed for live mode):
    pip install robin_stocks pyotp
"""

import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from .base_broker import Account, BaseBroker, Order, Position

LIVE_TRADING_ENV = "TRADINGAGENTS_LIVE_TRADING"
LIVE_TRADING_ACK = "I_UNDERSTAND_THE_RISKS"


class RobinhoodBroker(BaseBroker):
    name = "robinhood"
    is_live = True

    def __init__(self, execution_mode: str = "paper"):
        if execution_mode != "live":
            raise RuntimeError(
                "RobinhoodBroker requires config execution_mode='live'. "
                "Use PaperBroker for anything else."
            )
        if os.getenv(LIVE_TRADING_ENV) != LIVE_TRADING_ACK:
            raise RuntimeError(
                f"Live trading is locked. Export {LIVE_TRADING_ENV}={LIVE_TRADING_ACK} "
                "to acknowledge that real money can be lost."
            )

        try:
            import robin_stocks.robinhood as rh
        except ImportError as exc:
            raise ImportError(
                "robin_stocks is required for live Robinhood trading: "
                "pip install robin_stocks pyotp"
            ) from exc

        self.rh = rh
        username = os.getenv("RH_USERNAME")
        password = os.getenv("RH_PASSWORD")
        if not username or not password:
            raise RuntimeError("Set RH_USERNAME and RH_PASSWORD in the environment.")

        mfa_code = None
        mfa_secret = os.getenv("RH_MFA_SECRET")
        if mfa_secret:
            import pyotp

            mfa_code = pyotp.TOTP(mfa_secret).now()

        self.rh.login(username, password, mfa_code=mfa_code, store_session=True)

    # ------------------------------------------------------------------
    def get_account(self) -> Account:
        profile = self.rh.profiles.load_account_profile() or {}
        portfolio = self.rh.profiles.load_portfolio_profile() or {}

        def _f(d, key, default=0.0):
            try:
                return float(d.get(key) or default)
            except (TypeError, ValueError):
                return default

        equity = _f(portfolio, "equity") or _f(portfolio, "last_core_equity")
        cash = _f(profile, "cash")
        settled = _f(profile, "cash_available_for_withdrawal", cash)
        buying_power = _f(profile, "buying_power", cash)
        account_type = "margin" if _f(profile, "margin_limit") > 0 else "cash"
        return Account(
            equity=equity,
            cash=cash,
            settled_cash=settled,
            buying_power=buying_power,
            account_type=account_type,
        )

    def get_positions(self) -> List[Position]:
        holdings = self.rh.account.build_holdings() or {}
        positions = []
        for symbol, data in holdings.items():
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=float(data.get("quantity", 0)),
                    avg_price=float(data.get("average_buy_price", 0)),
                    last_price=float(data.get("price", 0)),
                )
            )
        return positions

    def get_quote(self, symbol: str) -> float:
        prices = self.rh.stocks.get_latest_price(symbol, includeExtendedHours=True)
        if not prices or prices[0] is None:
            raise ValueError(f"No quote available for {symbol}")
        return float(prices[0])

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
        if side == "buy":
            if order_type == "limit" and limit_price:
                result = self.rh.orders.order_buy_limit(
                    symbol, quantity, limit_price,
                    timeInForce=time_in_force, extendedHours=extended_hours,
                )
            else:
                result = self.rh.orders.order_buy_fractional_by_quantity(
                    symbol, quantity, timeInForce=time_in_force,
                    extendedHours=extended_hours,
                )
        elif side == "sell":
            if order_type == "limit" and limit_price:
                result = self.rh.orders.order_sell_limit(
                    symbol, quantity, limit_price,
                    timeInForce=time_in_force, extendedHours=extended_hours,
                )
            else:
                result = self.rh.orders.order_sell_fractional_by_quantity(
                    symbol, quantity, timeInForce=time_in_force,
                    extendedHours=extended_hours,
                )
        else:
            raise ValueError(f"Unknown side: {side}")

        result = result or {}
        return Order(
            order_id=result.get("id", str(uuid.uuid4())[:8]),
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            status=result.get("state", "submitted"),
            filled_at=datetime.now(timezone.utc).isoformat(),
            meta={"raw": {k: result.get(k) for k in ("id", "state", "reject_reason")}},
        )

    def place_stop_loss(self, symbol: str, quantity: float, stop_price: float) -> Order:
        result = self.rh.orders.order_sell_stop_loss(
            symbol, quantity, stop_price, timeInForce="gtc"
        ) or {}
        return Order(
            order_id=result.get("id", str(uuid.uuid4())[:8]),
            symbol=symbol,
            side="sell",
            quantity=quantity,
            order_type="stop",
            stop_price=stop_price,
            time_in_force="gtc",
            status=result.get("state", "submitted"),
            meta={"raw": {k: result.get(k) for k in ("id", "state", "reject_reason")}},
        )

    def cancel_open_orders(self, symbol: Optional[str] = None) -> int:
        open_orders = self.rh.orders.get_all_open_stock_orders() or []
        cancelled = 0
        for order in open_orders:
            if symbol:
                instrument = order.get("instrument", "")
                order_symbol = self.rh.stocks.get_symbol_by_url(instrument)
                if order_symbol != symbol:
                    continue
            self.rh.orders.cancel_stock_order(order["id"])
            cancelled += 1
        return cancelled
