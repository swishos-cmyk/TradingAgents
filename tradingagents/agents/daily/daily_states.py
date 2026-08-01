# TradingAgents/agents/daily/daily_states.py

from typing import Annotated

from langgraph.graph import MessagesState


class DailyTradingState(MessagesState):
    """State carried through the daily trading graph."""

    trade_date: Annotated[str, "Trading session date (yyyy-mm-dd)"]

    account_snapshot: Annotated[str, "JSON snapshot of account + open positions"]
    playbook: Annotated[str, "JSON playbook: watchlist, setups, evolving lessons"]

    scan_report: Annotated[str, "Opportunity scanner's ranked candidates"]
    selected_ticker: Annotated[str, "Ticker chosen for deep analysis"]

    setup_report: Annotated[str, "Technical setup analysis for the selected ticker"]
    catalyst_report: Annotated[str, "News/catalyst analysis for the selected ticker"]
    devils_advocate_report: Annotated[str, "Adversarial case against the trade"]

    trade_plan: Annotated[str, "Structured JSON trade plan from the trade planner"]
    risk_assessment: Annotated[str, "Risk officer's critique of the plan"]
    risk_verdict: Annotated[str, "Deterministic RiskGuard verdict (JSON)"]

    execution_report: Annotated[str, "What was actually executed at the broker"]
    journal_entry: Annotated[str, "End-of-run journal entry for the strategy lab"]
