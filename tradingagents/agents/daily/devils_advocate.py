# TradingAgents/agents/daily/devils_advocate.py

from .mission import MISSION_BRIEF


def create_devils_advocate(llm):
    """Adversarial reviewer: paid to kill the trade. No tools — argues
    purely from the evidence the analysts already gathered, which keeps
    the attack focused on reasoning quality."""

    def devils_advocate_node(state):
        ticker = state["selected_ticker"]

        prompt = f"""{MISSION_BRIEF}

ROLE: Devil's Advocate — the desk pays you to kill bad trades before
they cost money. You have no stake in being agreeable. If this trade is
mediocre, your job is to say so in plain language.

The desk is considering a LONG in `{ticker}` on {state['trade_date']}.

Attack the trade on every axis:
1. BASE RATES: what fraction of setups like this actually follow
   through? Breakouts fail more often than chart books admit; gaps fade;
   "obvious" momentum is usually late. Is this one statistically special
   or just familiar-looking?
2. CROWDING & TIMING: is the desk early, on time, or the exit liquidity
   for someone who bought two days ago?
3. THESIS FRAGILITY: name the single weakest link in the combined
   thesis. If that link snaps, how fast does the trade lose 1R?
4. HIDDEN EVENT RISK: anything the catalyst analyst may have missed —
   correlated names reporting earnings, macro prints, sector ETF weakness.
5. LEVEL QUALITY: are the proposed stop and target honest, or is the
   stop inside the noise (guaranteed to be hit) / the target beyond
   realistic reach (never hit before the thesis decays)?
6. OPPORTUNITY COST: this account can hold at most two positions. Is
   deploying settled cash here better than waiting for tomorrow's tape?

Rules of engagement:
- Argue from the evidence in the reports below; do not invent facts.
- Steelman the bull case in one sentence first, then dismantle what
  deserves dismantling.
- You are not required to reject the trade. If it genuinely survives
  your attack, say so — false vetoes cost compounding too.

End with exactly one line:
VERDICT: KILL — <reason>  |  VERDICT: PROCEED WITH CAUTION — <what to watch>  |  VERDICT: PROCEED — <why it survives>

---
SCANNER REPORT:
{state['scan_report']}

SETUP REPORT:
{state['setup_report']}

CATALYST REPORT:
{state['catalyst_report']}"""

        response = llm.invoke(prompt)

        return {
            "devils_advocate_report": response.content,
        }

    return devils_advocate_node
