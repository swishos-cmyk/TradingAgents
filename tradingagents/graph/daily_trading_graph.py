# TradingAgents/graph/daily_trading_graph.py
"""Daily trading graph: scan -> analyze -> challenge -> plan -> risk ->
execute -> journal, wired for Claude and a Robinhood (or paper) broker.

    START
      |
  Opportunity Scanner <-> tools_scan
      |  (NO_TRADE? -> Journal)
  Setup Analyst <-> tools_setup
      |
  Catalyst Analyst <-> tools_catalyst
      |
  Devil's Advocate
      |
  Trade Planner (memory-aware)
      |
  Risk Officer (LLM judgment + deterministic RiskGuard)
      |
  Executor (code only — places entry + protective stop at the broker)
      |
  Journal -> END
"""

import json
import os
from datetime import date
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import create_msg_delete
from tradingagents.agents.daily import (
    DailyTradingState,
    create_catalyst_analyst,
    create_devils_advocate,
    create_execution_agent,
    create_journal_node,
    create_opportunity_scanner,
    create_risk_officer,
    create_setup_analyst,
    create_strategy_lab,
    create_trade_planner,
    load_playbook,
)
from tradingagents.agents.daily.strategy_lab import load_memory_from_disk
from tradingagents.agents.daily.catalyst_analyst import CATALYST_TOOLS
from tradingagents.agents.daily.opportunity_scanner import SCANNER_TOOLS
from tradingagents.agents.daily.setup_analyst import SETUP_TOOLS
from tradingagents.agents.utils.memory import FinancialSituationMemory
from tradingagents.brokers import RiskGuard, create_broker, trade_ledger
from tradingagents.daily_config import DAILY_TRADING_CONFIG
from tradingagents.dataflows.config import set_config
from tradingagents.llm_clients import create_llm_client


class DailyConditionalLogic:
    """Routing for tool loops and the no-trade short circuit."""

    @staticmethod
    def route_scanner(state: DailyTradingState):
        if state["messages"][-1].tool_calls:
            return "tools_scan"
        selected = (state.get("selected_ticker") or "NO_TRADE").upper()
        if selected in ("NO_TRADE", ""):
            return "Msg Clear Scan (No Trade)"
        return "Msg Clear Scan"

    @staticmethod
    def route_setup(state: DailyTradingState):
        if state["messages"][-1].tool_calls:
            return "tools_setup"
        return "Msg Clear Setup"

    @staticmethod
    def route_catalyst(state: DailyTradingState):
        if state["messages"][-1].tool_calls:
            return "tools_catalyst"
        return "Msg Clear Catalyst"


class DailyTradingGraph:
    """Orchestrates the daily trading desk."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, debug: bool = False):
        self.debug = debug
        self.config = {**DAILY_TRADING_CONFIG, **(config or {})}
        set_config(self.config)

        state_dir = os.path.join(self.config["results_dir"], "daily_trading")
        os.makedirs(state_dir, exist_ok=True)
        self.playbook_path = os.path.join(state_dir, "playbook.json")
        self.journal_path = os.path.join(state_dir, "journal.jsonl")
        self.ledger_path = os.path.join(state_dir, "open_trades.json")
        self.memory_path = os.path.join(state_dir, "memory.json")

        # broker + deterministic risk engine
        self.broker = create_broker(self.config)
        self.risk_guard = RiskGuard(
            state_path=os.path.join(state_dir, "risk_state.json"),
            params=self.config.get("risk_params"),
        )

        # Claude clients: deep model for judgment nodes, quick for tool loops
        llm_kwargs = {}
        if self.config.get("anthropic_effort"):
            llm_kwargs["effort"] = self.config["anthropic_effort"]
        deep = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        self.deep_llm = deep.get_llm()
        self.quick_llm = quick.get_llm()

        self.trader_memory = FinancialSituationMemory("daily_trader_memory", self.config)
        load_memory_from_disk(self.trader_memory, self.memory_path)
        self.strategy_lab = create_strategy_lab(
            self.deep_llm,
            self.playbook_path,
            self.journal_path,
            self.trader_memory,
            memory_path=self.memory_path,
        )

        self.graph = self._build_graph()

    # ------------------------------------------------------------------
    def _build_graph(self):
        logic = DailyConditionalLogic()

        scanner = create_opportunity_scanner(self.quick_llm)
        setup = create_setup_analyst(self.quick_llm)
        catalyst = create_catalyst_analyst(self.quick_llm)
        advocate = create_devils_advocate(self.deep_llm)
        planner = create_trade_planner(self.deep_llm, self.trader_memory)
        risk_officer = create_risk_officer(self.deep_llm, self.risk_guard)
        executor = create_execution_agent(self.broker, self.ledger_path)
        journal = create_journal_node(self.journal_path)

        workflow = StateGraph(DailyTradingState)

        workflow.add_node("Opportunity Scanner", scanner)
        workflow.add_node("tools_scan", ToolNode(SCANNER_TOOLS))
        workflow.add_node("Msg Clear Scan", create_msg_delete())
        workflow.add_node("Msg Clear Scan (No Trade)", create_msg_delete())

        workflow.add_node("Setup Analyst", setup)
        workflow.add_node("tools_setup", ToolNode(SETUP_TOOLS))
        workflow.add_node("Msg Clear Setup", create_msg_delete())

        workflow.add_node("Catalyst Analyst", catalyst)
        workflow.add_node("tools_catalyst", ToolNode(CATALYST_TOOLS))
        workflow.add_node("Msg Clear Catalyst", create_msg_delete())

        workflow.add_node("Devils Advocate", advocate)
        workflow.add_node("Trade Planner", planner)
        workflow.add_node("Risk Officer", risk_officer)
        workflow.add_node("Executor", executor)
        workflow.add_node("Journal", journal)

        workflow.add_edge(START, "Opportunity Scanner")
        workflow.add_conditional_edges(
            "Opportunity Scanner",
            logic.route_scanner,
            ["tools_scan", "Msg Clear Scan", "Msg Clear Scan (No Trade)"],
        )
        workflow.add_edge("tools_scan", "Opportunity Scanner")
        workflow.add_edge("Msg Clear Scan (No Trade)", "Journal")
        workflow.add_edge("Msg Clear Scan", "Setup Analyst")

        workflow.add_conditional_edges(
            "Setup Analyst", logic.route_setup, ["tools_setup", "Msg Clear Setup"]
        )
        workflow.add_edge("tools_setup", "Setup Analyst")
        workflow.add_edge("Msg Clear Setup", "Catalyst Analyst")

        workflow.add_conditional_edges(
            "Catalyst Analyst",
            logic.route_catalyst,
            ["tools_catalyst", "Msg Clear Catalyst"],
        )
        workflow.add_edge("tools_catalyst", "Catalyst Analyst")
        workflow.add_edge("Msg Clear Catalyst", "Devils Advocate")

        workflow.add_edge("Devils Advocate", "Trade Planner")
        workflow.add_edge("Trade Planner", "Risk Officer")
        workflow.add_edge("Risk Officer", "Executor")
        workflow.add_edge("Executor", "Journal")
        workflow.add_edge("Journal", END)

        return workflow.compile()

    # ------------------------------------------------------------------
    def _account_snapshot(self) -> str:
        account = self.broker.get_account()
        positions = [p.to_dict() for p in self.broker.get_positions()]
        return json.dumps(
            {"account": account.to_dict(), "positions": positions}, indent=2
        )

    def _record_exit(self, trade: Optional[Dict], trade_date: str) -> None:
        """A same-session open-and-close is a day trade for PDT purposes."""
        if trade and trade.get("entry_date") == trade_date:
            self.risk_guard.record_day_trade(trade["symbol"], on_date=trade_date)

    def manage_open_positions(self, trade_date: str) -> List[str]:
        """Enforce the accepted plans on existing positions before any new
        decision: sweep breached stops, take profits at target, and close
        anything past its max holding period. Returns report lines."""
        lines = []

        # 1. paper broker: fill any protective stops breached since last run
        if hasattr(self.broker, "process_stops"):
            for order in self.broker.process_stops():
                trade = trade_ledger.close_trade(
                    self.ledger_path, order.symbol, trade_date,
                    order.filled_price, reason="stop_loss",
                )
                self._record_exit(trade, trade_date)
                lines.append(
                    f"Stop filled: SELL {order.quantity} {order.symbol} "
                    f"@ ${order.filled_price:.4f}"
                )

        held = {p.symbol: p for p in self.broker.get_positions()}

        for trade in trade_ledger.open_trades(self.ledger_path):
            symbol = trade["symbol"]

            # position gone (e.g. live stop filled at the broker): reconcile
            if symbol not in held:
                closed = trade_ledger.close_trade(
                    self.ledger_path, symbol, trade_date, None,
                    reason="closed_at_broker",
                )
                self._record_exit(closed, trade_date)
                lines.append(f"Reconciled: {symbol} no longer held; ledger closed.")
                continue

            try:
                quote = self.broker.get_quote(symbol)
            except Exception as exc:
                lines.append(f"Could not quote {symbol} ({exc}); position left as-is.")
                continue

            days_held = (
                date.fromisoformat(trade_date)
                - date.fromisoformat(trade["entry_date"])
            ).days  # calendar days — conservative vs. trading days
            hit_target = trade.get("take_profit") and quote >= trade["take_profit"]
            time_out = days_held >= int(trade.get("max_holding_days") or 2)
            if not (hit_target or time_out):
                continue

            reason = "take_profit" if hit_target else "max_holding_days"
            qty = min(trade["quantity"], held[symbol].quantity)
            try:
                self.broker.cancel_open_orders(symbol)
                order = self.broker.place_order(symbol, "sell", qty, order_type="market")
                closed = trade_ledger.close_trade(
                    self.ledger_path, symbol, trade_date,
                    order.filled_price, reason=reason,
                )
                self._record_exit(closed, trade_date)
                lines.append(
                    f"Exit ({reason}): SELL {qty} {symbol}"
                    + (f" @ ${order.filled_price:.4f}" if order.filled_price else "")
                )
            except Exception as exc:
                lines.append(f"EXIT FAILED for {symbol} ({reason}): {exc}")

        return lines

    def run_session(self, trade_date: str) -> Dict[str, Any]:
        """Run one full trading session for the given date."""
        management_report = self.manage_open_positions(trade_date)

        playbook = load_playbook(self.playbook_path)
        management_note = (
            "Position management already ran this morning:\n"
            + "\n".join(management_report)
            if management_report
            else "No open positions required management this morning."
        )
        init_state = {
            "messages": [
                (
                    "human",
                    f"Run the daily trading session for {trade_date}. "
                    f"{management_note}\n"
                    "Begin by scanning the playbook watchlist.",
                )
            ],
            "trade_date": trade_date,
            "account_snapshot": self._account_snapshot(),
            "playbook": json.dumps(playbook, indent=2),
            "selected_ticker": "",
            "scan_report": "",
            "setup_report": "",
            "catalyst_report": "",
            "devils_advocate_report": "",
            "trade_plan": json.dumps({"action": "NO_TRADE", "symbol": ""}),
            "risk_assessment": "",
            "risk_verdict": json.dumps({"approved": False, "reasons": []}),
            "execution_report": "",
            "journal_entry": "",
        }

        args = {"config": {"recursion_limit": self.config.get("max_recur_limit", 100)}}
        if self.debug:
            trace = []
            for chunk in self.graph.stream(init_state, stream_mode="values", **args):
                if chunk.get("messages"):
                    chunk["messages"][-1].pretty_print()
                trace.append(chunk)
            final_state = trace[-1]
        else:
            final_state = self.graph.invoke(init_state, **args)

        if management_report:
            final_state["execution_report"] = (
                "PRE-SESSION POSITION MANAGEMENT:\n"
                + "\n".join(management_report)
                + "\n\n"
                + final_state.get("execution_report", "")
            )
        self._save_session_log(trade_date, final_state)
        return final_state

    def reflect(self, trade_date: str, realized_pnl_note: str = "") -> str:
        """Nightly strategy-lab reflection: updates the playbook."""
        return self.strategy_lab(trade_date, realized_pnl_note)

    def _save_session_log(self, trade_date: str, state: Dict[str, Any]) -> None:
        log_dir = os.path.join(
            self.config["results_dir"], "daily_trading", "session_logs"
        )
        os.makedirs(log_dir, exist_ok=True)
        keys = (
            "trade_date", "account_snapshot", "scan_report", "selected_ticker",
            "setup_report", "catalyst_report", "devils_advocate_report",
            "trade_plan", "risk_assessment", "risk_verdict",
            "execution_report", "journal_entry",
        )
        payload = {k: state.get(k, "") for k in keys}
        with open(
            os.path.join(log_dir, f"session_{trade_date}.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(payload, f, indent=2)
