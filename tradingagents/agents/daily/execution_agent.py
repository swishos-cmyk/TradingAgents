# TradingAgents/agents/daily/execution_agent.py
"""Execution node — deliberately NOT an LLM.

By the time flow reaches here every judgment call has been made; what
remains is faithful order placement, protective-stop attachment, and an
accurate record of what happened. Code does that better than prose.

Order of operations matters: the protective stop is attached only AFTER
the entry fill is confirmed, and only for the confirmed quantity — a
stop for shares you do not hold yet gets rejected at the broker, and the
entry could then fill later with no protection."""

import json
import time
from datetime import datetime, timezone

from tradingagents.brokers import trade_ledger

FILL_POLL_ATTEMPTS = 12
FILL_POLL_INTERVAL_S = 5


def _confirm_fill(broker, order):
    """Return (filled_quantity, fill_price, status). Polls live brokers
    until the entry fills or the attempts run out; cancels on timeout so
    an unmonitored entry can't fill later without a stop."""
    if order.status == "filled":
        return order.quantity, order.filled_price, "filled"
    if order.status in ("unfilled", "rejected", "cancelled", "failed"):
        return 0.0, None, order.status

    for _ in range(FILL_POLL_ATTEMPTS):
        time.sleep(FILL_POLL_INTERVAL_S)
        latest = broker.get_order(order.order_id)
        if latest is None:
            continue
        if latest.status == "filled" and latest.quantity > 0:
            return latest.quantity, latest.filled_price, "filled"
        if latest.status in ("rejected", "cancelled", "failed"):
            return 0.0, None, latest.status

    # Timed out still pending: cancel so the entry can't fill unprotected.
    try:
        broker.cancel_open_orders(order.symbol)
        return 0.0, None, "cancelled_unfilled_timeout"
    except Exception as exc:
        return 0.0, None, f"pending_cancel_failed ({exc})"


def create_execution_agent(broker, ledger_path):
    def execution_node(state):
        plan = json.loads(state["trade_plan"])
        verdict = json.loads(state["risk_verdict"])

        lines = [f"EXECUTION REPORT — {state['trade_date']} ({broker.name} broker)"]

        if plan.get("action") != "TRADE" or not verdict.get("approved"):
            reason = "; ".join(verdict.get("reasons", [])) or "no approved plan"
            lines.append(f"No order placed: {reason}")
            return {"execution_report": "\n".join(lines)}

        symbol = plan["symbol"]
        quantity = float(verdict["quantity"])
        entry_type = plan.get("entry_type", "limit")
        entry_price = float(plan.get("entry_price") or 0)
        stop_loss = float(plan.get("stop_loss") or 0)
        take_profit = float(plan.get("take_profit") or 0)

        try:
            order = broker.place_order(
                symbol=symbol,
                side="buy",
                quantity=quantity,
                order_type=entry_type,
                limit_price=entry_price if entry_type == "limit" else None,
                time_in_force="gfd",
            )
        except Exception as exc:
            lines.append(f"ENTRY ORDER FAILED for {symbol}: {exc}")
            return {"execution_report": "\n".join(lines)}

        filled_qty, fill_price, fill_status = _confirm_fill(broker, order)
        lines.append(
            f"BUY {quantity} {symbol} ({entry_type}"
            + (f" @ ${entry_price:.2f}" if entry_type == "limit" else "")
            + f") -> {fill_status}"
            + (f", filled {filled_qty} @ ${fill_price:.4f}" if filled_qty else "")
        )

        if filled_qty <= 0:
            lines.append("No fill confirmed — no stop placed, no position recorded.")
            return {"execution_report": "\n".join(lines)}

        if stop_loss > 0:
            try:
                stop_order = broker.place_stop_loss(symbol, filled_qty, stop_loss)
                lines.append(
                    f"Protective stop SELL {filled_qty} {symbol} @ ${stop_loss:.2f} "
                    f"-> status={stop_order.status}"
                )
            except Exception as exc:
                lines.append(
                    f"WARNING: stop-loss placement FAILED ({exc}). "
                    "Position is unprotected — manual stop required."
                )

        trade_ledger.open_trade(
            ledger_path,
            symbol=symbol,
            quantity=filled_qty,
            entry_date=state["trade_date"],
            entry_price=fill_price or entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_days=int(plan.get("max_holding_days") or 2),
        )
        lines.append(
            f"Ledger: open trade recorded — target ${take_profit:.2f}, "
            f"max hold {plan.get('max_holding_days', '?')} day(s), "
            f"invalidation: {plan.get('invalidation', 'n/a')}"
        )
        lines.append(f"Placed at {datetime.now(timezone.utc).isoformat()}")
        return {"execution_report": "\n".join(lines)}

    return execution_node
