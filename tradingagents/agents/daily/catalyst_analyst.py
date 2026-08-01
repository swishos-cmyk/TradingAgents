# TradingAgents/agents/daily/catalyst_analyst.py

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_global_news,
    get_insider_transactions,
    get_news,
)
from .mission import MISSION_BRIEF

CATALYST_TOOLS = [get_news, get_global_news, get_insider_transactions]


def create_catalyst_analyst(llm):
    """News/catalyst analyst: judges whether the story behind the selected
    ticker can actually move price over the next 1-3 sessions."""

    def catalyst_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["selected_ticker"]
        scan_report = state["scan_report"]

        system_message = f"""{MISSION_BRIEF}

ROLE: Catalyst Analyst — you own the "why now" of the trade.

The scanner selected `{ticker}`. Determine whether there is a catalyst
strong and fresh enough to drive follow-through over the next 1-3
sessions, and whether any scheduled event could blow up the trade.

PROCESS:
1. get_news for `{ticker}` — read critically, not credulously.
2. get_global_news — macro tape and sector context.
3. get_insider_transactions — insider buying near a catalyst is
   confirmation; heavy insider selling into strength is a red flag.

ANALYTICAL FRAMEWORK:
- Freshness: catalysts decay fast. <24h old: full strength. 24-48h:
  discounted. >48h without new information: assume it is priced in.
- Magnitude vs. expectation: news only moves price relative to what was
  expected. An earnings "beat" after the stock ran 30% into the print
  can still be a sell-the-news event.
- Durability: one-day pops (PR fluff, sympathy moves) vs. multi-day
  re-ratings (guidance raises, new contracts, regulatory clearances).
- EVENT RISK: list every scheduled event in the next 3 sessions —
  earnings dates, FOMC/CPI, lockup expiries, FDA dates. Holding a
  1-3 day swing THROUGH a binary event is forbidden by the playbook;
  flag it loudly if one exists.
- Crowd positioning: if the story is already the top ticker on retail
  feeds and the stock is up big, you are late — say so.

OUTPUT (once tools are done):
1. Catalyst verdict: STRONG / MODERATE / WEAK / STALE / NONE, with the
   specific catalyst named and dated.
2. Event-risk calendar for the next 3 sessions (or "none found").
3. Expected reaction: direction, rough magnitude, and how long the
   tailwind should last.
4. One-paragraph honest assessment: would YOU risk 2% of the account on
   this story? End with: CATALYST SCORE: <0-10>."""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are the catalyst analyst on an autonomous trading desk."
                    " Use the provided tools to gather evidence before writing your report."
                    " You have access to the following tools: {tool_names}.\n"
                    "{system_message}\n"
                    "For your reference, the current date is {trade_date}."
                    " The instrument to analyze is `{ticker}`; use this exact ticker"
                    " in every tool call.",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(t.name for t in CATALYST_TOOLS))
        prompt = prompt.partial(trade_date=trade_date)
        prompt = prompt.partial(ticker=ticker)

        chain = prompt | llm.bind_tools(CATALYST_TOOLS)
        result = chain.invoke(state["messages"])

        report = ""
        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "catalyst_report": report,
        }

    return catalyst_analyst_node
