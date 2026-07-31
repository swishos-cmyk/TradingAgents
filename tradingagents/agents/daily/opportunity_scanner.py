# TradingAgents/agents/daily/opportunity_scanner.py

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import get_news, get_global_news
from .daily_tools import get_realtime_quote, get_watchlist_snapshot
from .mission import MISSION_BRIEF

SCANNER_TOOLS = [get_watchlist_snapshot, get_realtime_quote, get_news, get_global_news]


def create_opportunity_scanner(llm):
    """Pre-market scanner: ranks the playbook watchlist and picks at most
    one ticker for deep analysis."""

    def opportunity_scanner_node(state):
        trade_date = state["trade_date"]
        playbook = state["playbook"]
        account_snapshot = state["account_snapshot"]

        system_message = f"""{MISSION_BRIEF}

ROLE: Opportunity Scanner — the desk's first pass each morning.

Your job: from the playbook watchlist (plus any tickers the playbook's
lessons flag), find the SINGLE best candidate for today, or declare
NO_TRADE if nothing qualifies.

PROCESS (follow in order):
1. Call get_watchlist_snapshot with the full watchlist for {trade_date}.
2. Call get_global_news once to check the macro tape (FOMC, CPI, jobs
   numbers, geopolitical shocks). On a high-event-risk day, raise the bar.
3. For the 2-3 most interesting rows, call get_news to check for a fresh,
   *tradeable* catalyst (earnings beat, guidance raise, FDA news, contract
   win, sector momentum). Stale news is not a catalyst.
4. Optionally confirm live prices with get_realtime_quote.

WHAT AN A+ CANDIDATE LOOKS LIKE (small-account daily strategy):
- Relative volume >= 1.5x — the crowd is already there; you need the
  liquidity and the follow-through.
- A catalyst less than 48 hours old, or a technically pristine
  continuation pattern (flag near 20-day highs, ATR% >= 3 so the move can
  pay, but < 12 so the stop isn't untradeable).
- Price roughly $2-$400. Below $2: manipulation and halt risk. The
  account can buy fractional shares, so high price is fine if liquid.
- NOT: binary-event biotech before the readout, meme squeezes already up
  100%+ on the day, or anything you'd need luck rather than structure to exit.

SCORING: rate each serious candidate 0-10 on (a) catalyst freshness &
strength, (b) technical structure, (c) liquidity/rel-vol, (d) risk
definition (is there an obvious stop?). Only a composite >= 7 earns deep
analysis.

OUTPUT (when you are done with tools, write the report — no more calls):
1. A ranked table of the top 5 candidates with scores and one-line theses.
2. Macro/event-risk note for today.
3. Final line, exactly one of:
   SELECTED: <TICKER> — <one-sentence reason>
   SELECTED: NO_TRADE — <one-sentence reason>

Current playbook (watchlist, evolving lessons, parameters):
{playbook}

Account snapshot:
{account_snapshot}"""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are the opportunity scanner on an autonomous trading desk."
                    " Use the provided tools to gather evidence before deciding."
                    " You have access to the following tools: {tool_names}.\n"
                    "{system_message}\n"
                    "For your reference, the current date is {trade_date}.",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join(t.name for t in SCANNER_TOOLS))
        prompt = prompt.partial(trade_date=trade_date)

        chain = prompt | llm.bind_tools(SCANNER_TOOLS)
        result = chain.invoke(state["messages"])

        report = ""
        selected = ""
        if len(result.tool_calls) == 0:
            report = result.content
            selected = _extract_selection(result.content)

        return {
            "messages": [result],
            "scan_report": report,
            "selected_ticker": selected,
        }

    return opportunity_scanner_node


def _extract_selection(report: str) -> str:
    """Pull the ticker (or NO_TRADE) off the final SELECTED line."""
    for line in reversed(report.strip().splitlines()):
        line = line.strip().lstrip("*# ").rstrip()
        if line.upper().startswith("SELECTED:"):
            rest = line.split(":", 1)[1].replace("—", " ").strip()
            if rest:
                token = rest.split()[0].strip("*`.,;: ").upper()
                if token:
                    return token
    return "NO_TRADE"
