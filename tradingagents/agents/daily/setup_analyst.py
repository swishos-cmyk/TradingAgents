# TradingAgents/agents/daily/setup_analyst.py

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import get_indicators, get_stock_data
from .daily_tools import get_realtime_quote
from .mission import MISSION_BRIEF

SETUP_TOOLS = [get_stock_data, get_indicators, get_realtime_quote]


def create_setup_analyst(llm):
    """Technical analyst specialized in short-horizon setups: defines the
    exact entry, stop, and target structure for the selected ticker."""

    def setup_analyst_node(state):
        trade_date = state["trade_date"]
        ticker = state["selected_ticker"]
        scan_report = state["scan_report"]
        playbook = state["playbook"]

        system_message = f"""{MISSION_BRIEF}

ROLE: Setup Analyst — you own the price structure of the trade.

The scanner selected `{ticker}`. Your job is to determine whether it has
an A+ short-horizon (1-3 day hold) LONG setup and, if so, define the
exact levels. You are the only agent who sets prices; be precise.

PROCESS:
1. get_stock_data for the last ~60 sessions ending {trade_date}.
2. get_indicators — choose what fits the setup, typically: close_10_ema,
   close_20_sma (via boll), rsi, macd, atr, vwma. ATR is mandatory: stops
   and targets are quoted in ATR multiples.
3. get_realtime_quote to anchor levels to the live price.

SETUP TAXONOMY (classify as exactly one, or NONE):
- BREAKOUT: consolidation near 20-day highs resolving upward on volume.
  Entry: above the trigger level. Stop: below consolidation low or 1.5 ATR.
- MOMENTUM_CONTINUATION: strong trend (price > rising 10 EMA), pulled
  back 1-3 days on declining volume, resuming. Entry: reclaim of the
  10 EMA / prior high. Stop: below the pullback low.
- GAP_AND_GO: fresh catalyst gap up 3-10% holding above the opening
  range. Entry: opening-range high. Stop: below VWAP-area / range low.
- OVERSOLD_REVERSAL: quality name flushed to a major support with RSI
  < 30 and a reversal bar. Entry: above the reversal bar. Stop: below
  the flush low. (Counter-trend — demand extra confirmation.)
- NONE: structure is not there. Say so plainly; you kill more trades
  than you bless.

LEVEL DISCIPLINE:
- Stop distance: 1.0-2.0 ATR from entry; never inside the day's noise.
- Target: nearest meaningful resistance, must be >= 2.0x the stop
  distance (>= 2R). If the chart cannot pay 2R, the setup is NONE.
- Quote every level to the cent and state the ATR you used.

OUTPUT (once tools are done):
1. Setup classification and conviction 0-10.
2. The levels block, exactly in this form:
   LEVELS:
   entry_type: <limit|market>
   entry_price: <x.xx>
   stop_loss: <x.xx>
   take_profit: <x.xx>
   atr: <x.xx>
   max_holding_days: <1|2|3>
3. What would invalidate the setup before entry (gap over the trigger,
   market-wide reversal, etc.).
4. A Markdown table summarizing key indicator readings.

Scanner's report for context:
{scan_report}

Playbook (respect its lessons about which setups have been working):
{playbook}"""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are the setup analyst on an autonomous trading desk."
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
        prompt = prompt.partial(tool_names=", ".join(t.name for t in SETUP_TOOLS))
        prompt = prompt.partial(trade_date=trade_date)
        prompt = prompt.partial(ticker=ticker)

        chain = prompt | llm.bind_tools(SETUP_TOOLS)
        result = chain.invoke(state["messages"])

        report = ""
        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "setup_report": report,
        }

    return setup_analyst_node
