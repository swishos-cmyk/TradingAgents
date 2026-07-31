# TradingAgents/agents/daily/execution_agent.py
"""Execution node — deliberately NOT an LLM.

By the time flow reaches here every judgment call has been made; what
remains is faithful order placement, protective-stop attachment, and an
accurate record of what happened. Code does that better than prose."""

import json
from datetime import datetime, timezone


def create_execution_agent(broker):
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

        try:
            order = broker.place_order(
                symbol=symbol,
                side="buy",
                quantity=quantity,
                order_type=entry_type,
                limit_price=entry_price if entry_type == "limit" else None,
                time_in_force="gfd",
            )
            lines.append(
                f"BUY {quantity} {symbol} ({entry_type}"
                + (f" @ ${entry_price:.2f}" if entry_type == "limit" else "")
                + f") -> status={order.status}"
                + (f", filled @ ${order.filled_price:.4f}" if order.filled_price else "")
            )
        except Exception as exc:
            lines.append(f"ENTRY ORDER FAILED for {symbol}: {exc}")
            return {"execution_report": "\n".join(lines)}

        if stop_loss > 0:
            try:
                stop_order = broker.place_stop_loss(symbol, quantity, stop_loss)
                lines.append(
                    f"Protective stop SELL {quantity} {symbol} @ ${stop_loss:.2f} "
                    f"-> status={stop_order.status}"
                )
            except Exception as exc:
                lines.append(
                    f"WARNING: stop-loss placement FAILED ({exc}). "
                    "Position is unprotected — manual stop required."
                )

        lines.append(
            f"Plan on record: target ${float(plan.get('take_profit') or 0):.2f}, "
            f"max hold {plan.get('max_holding_days', '?')} day(s), "
            f"invalidation: {plan.get('invalidation', 'n/a')}"
        )
        lines.append(f"Placed at {datetime.now(timezone.utc).isoformat()}")
        return {"execution_report": "\n".join(lines)}

    return execution_node
