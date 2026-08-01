# TradingAgents/agents/daily/trade_planner.py

import json
import re

from .mission import MISSION_BRIEF

TRADE_PLAN_SCHEMA = """{
  "action": "TRADE" | "NO_TRADE",
  "symbol": "<ticker or empty string>",
  "side": "buy",
  "setup": "<BREAKOUT|MOMENTUM_CONTINUATION|GAP_AND_GO|OVERSOLD_REVERSAL>",
  "entry_type": "limit" | "market",
  "entry_price": <float>,
  "stop_loss": <float>,
  "take_profit": <float>,
  "max_holding_days": <int 1-3>,
  "confidence": <float 0.0-1.0>,
  "thesis": "<two sentences max>",
  "invalidation": "<what kills the trade before/after entry>"
}"""


def create_trade_planner(llm, memory):
    """Synthesizes all reports into a single machine-readable trade plan.
    Uses the trader memory so past reflections shape today's plan."""

    def trade_planner_node(state):
        ticker = state["selected_ticker"]

        situation = (
            f"{state['scan_report']}\n\n{state['setup_report']}\n\n"
            f"{state['catalyst_report']}"
        )
        past_memories = memory.get_memories(situation, n_matches=2)
        past_memory_str = "\n\n".join(
            rec["recommendation"] for rec in past_memories
        ) or "No relevant past lessons yet."

        prompt = f"""{MISSION_BRIEF}

ROLE: Trade Planner — you convert the desk's research into one
machine-readable decision. Downstream, a deterministic risk guard sizes
the position; you do NOT choose share counts, only levels and conviction.

Decision rules:
- The devil's advocate said KILL → your action is NO_TRADE unless you
  can rebut the specific kill reason with evidence from the reports
  (state the rebuttal in "thesis"). Vague optimism is not a rebuttal.
- Setup report says NONE, or catalyst score < 5, or reward:risk < 2 →
  NO_TRADE.
- Use the setup analyst's LEVELS block verbatim unless the devil's
  advocate exposed a concrete flaw in a level; if you adjust, keep the
  2R minimum intact and explain in "thesis".
- Confidence calibration: 0.8+ means "textbook setup, fresh catalyst,
  clean tape" — expect this less than once a week. Most approved trades
  are 0.6-0.75.

Lessons from your past trades in similar situations:
{past_memory_str}

Respond with ONLY a JSON object (no markdown fences, no commentary)
matching this schema exactly:
{TRADE_PLAN_SCHEMA}

---
TICKER UNDER CONSIDERATION: {ticker}
TRADE DATE: {state['trade_date']}

SCANNER REPORT:
{state['scan_report']}

SETUP REPORT:
{state['setup_report']}

CATALYST REPORT:
{state['catalyst_report']}

DEVIL'S ADVOCATE:
{state['devils_advocate_report']}"""

        response = llm.invoke(prompt)
        plan_json = _coerce_plan_json(response.content)

        return {
            "trade_plan": plan_json,
        }

    return trade_planner_node


def _coerce_plan_json(raw: str) -> str:
    """Extract and validate the JSON plan; fall back to NO_TRADE on any
    parse failure so downstream nodes always get valid JSON."""
    text = raw.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        plan = json.loads(text)
        if not isinstance(plan, dict) or "action" not in plan:
            raise ValueError("missing action")
        if plan["action"] not in ("TRADE", "NO_TRADE"):
            raise ValueError(f"bad action: {plan['action']}")
        return json.dumps(plan, indent=2)
    except (json.JSONDecodeError, ValueError) as exc:
        return json.dumps(
            {
                "action": "NO_TRADE",
                "symbol": "",
                "thesis": f"Planner output was not valid JSON ({exc}); defaulting to NO_TRADE.",
                "raw_output": raw[:2000],
            },
            indent=2,
        )
