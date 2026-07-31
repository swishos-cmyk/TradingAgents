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
from typing import Any, Dict, Optional

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
from tradingagents.agents.daily.catalyst_analyst import CATALYST_TOOLS
from tradingagents.agents.daily.opportunity_scanner import SCANNER_TOOLS
from tradingagents.agents.daily.setup_analyst import SETUP_TOOLS
from tradingagents.agents.utils.memory import FinancialSituationMemory
from tradingagents.brokers import RiskGuard, create_broker
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
        self.strategy_lab = create_strategy_lab(
            self.deep_llm, self.playbook_path, self.journal_path, self.trader_memory
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
        executor = create_execution_agent(self.broker)
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

    def run_session(self, trade_date: str) -> Dict[str, Any]:
        """Run one full trading session for the given date."""
        # paper broker: sweep any protective stops breached since last run
        if hasattr(self.broker, "process_stops"):
            triggered = self.broker.process_stops()
            for order in triggered:
                self.risk_guard.record_day_trade(order.symbol, on_date=trade_date)

        playbook = load_playbook(self.playbook_path)
        init_state = {
            "messages": [
                (
                    "human",
                    f"Run the daily trading session for {trade_date}. Begin by "
                    "scanning the playbook watchlist.",
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
