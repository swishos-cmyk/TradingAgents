# TradingAgents/agents/daily/risk_officer.py

import json

from .mission import MISSION_BRIEF


def create_risk_officer(llm, risk_guard):
    """Two-layer risk check.

    Layer 1 (LLM): a chief-risk-officer critique that can veto on
    judgment grounds the deterministic guard cannot see (thesis quality,
    correlation with an existing position, event risk).

    Layer 2 (code): the RiskGuard circuit breakers and position sizing.
    The LLM cannot override layer 2 in either direction.
    """

    def risk_officer_node(state):
        plan = json.loads(state["trade_plan"])
        account = json.loads(state["account_snapshot"])

        # NO_TRADE plans skip straight through with a stamped verdict.
        if plan.get("action") != "TRADE":
            verdict = {
                "approved": False,
                "quantity": 0,
                "notional": 0,
                "reasons": ["Planner decided NO_TRADE."],
                "halted": False,
            }
            return {
                "risk_assessment": "No trade proposed; nothing to assess.",
                "risk_verdict": json.dumps(verdict, indent=2),
            }

        # ---- Layer 1: LLM judgment ------------------------------------
        prompt = f"""{MISSION_BRIEF}

ROLE: Chief Risk Officer. A deterministic risk engine will enforce hard
limits (per-trade risk, position caps, daily halt, settled funds) after
you — do not re-derive those numbers. Your job is the judgment layer the
machine cannot do:

1. Thesis-vs-levels coherence: does the stop actually sit where the
   thesis is invalidated, or is it an arbitrary number?
2. Portfolio interaction: given current positions (below), does this
   trade concentrate the account in one theme/sector/factor?
3. Event exposure: does max_holding_days carry the position through any
   scheduled binary event flagged in the research?
4. Process integrity: did the planner genuinely rebut a KILL verdict
   from the devil's advocate, or hand-wave past it?
5. Regime sanity: in a broadly risk-off tape, longs need a higher bar.

Be brief and concrete. End with exactly one line:
RISK OFFICER: APPROVE  or  RISK OFFICER: VETO — <reason>

---
PROPOSED PLAN:
{state['trade_plan']}

ACCOUNT AND POSITIONS:
{state['account_snapshot']}

DEVIL'S ADVOCATE REPORT:
{state['devils_advocate_report']}

CATALYST REPORT (for the event calendar):
{state['catalyst_report']}"""

        response = llm.invoke(prompt)
        assessment = response.content

        llm_veto = "RISK OFFICER: VETO" in assessment.upper()

        # ---- Layer 2: deterministic guard ------------------------------
        open_positions = len(account.get("positions", []))
        already_holding = any(
            p.get("symbol") == plan.get("symbol")
            for p in account.get("positions", [])
        )

        guard_verdict = risk_guard.evaluate(
            plan=plan,
            account=account.get("account", account),
            open_positions=open_positions,
            is_day_trade=plan.get("max_holding_days", 2) == 0,
            on_date=state["trade_date"],
        )

        verdict = guard_verdict.to_dict()
        if already_holding:
            verdict["approved"] = False
            verdict["reasons"].append(
                f"Already holding {plan.get('symbol')}; no adding in v1."
            )
        if llm_veto:
            verdict["approved"] = False
            verdict["reasons"].append("Vetoed by risk officer (judgment layer).")

        return {
            "risk_assessment": assessment,
            "risk_verdict": json.dumps(verdict, indent=2),
        }

    return risk_officer_node
