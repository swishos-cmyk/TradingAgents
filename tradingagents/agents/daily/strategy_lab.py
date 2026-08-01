# TradingAgents/agents/daily/strategy_lab.py
"""Strategy Lab — the self-improvement loop.

Journals every session, and (on reflection runs) rewrites the playbook:
watchlist rotation, setup win-rate ledger, and distilled lessons. The
playbook is the one artifact the desk "owns" across days; every agent
prompt embeds it, so a lesson learned Monday changes behavior Tuesday.

Guardrails: the lab may tune parameters ONLY within hard bounds — it can
make the desk more selective, never more reckless than the RiskGuard.
"""

import json
import os
import re
from datetime import datetime, timezone

from .mission import MISSION_BRIEF

DEFAULT_PLAYBOOK = {
    "version": 1,
    "updated_at": None,
    "watchlist": [
        # liquid, high-beta names a small account can trade cleanly
        "TSLA", "NVDA", "AMD", "PLTR", "SOFI", "HOOD", "COIN", "MARA",
        "RIOT", "AAL", "F", "NIO", "RIVN", "SMCI", "IONQ", "RKLB",
        "UPST", "AFRM", "DKNG", "CELH",
    ],
    "setup_stats": {},  # e.g. "BREAKOUT": {"wins": 0, "losses": 0}
    "lessons": [
        "No trades in the first 15 minutes after the open; let the range form.",
        "Never hold a swing through a scheduled binary event (earnings, FDA, FOMC).",
        "If the devil's advocate and risk officer both hesitate, the answer is no.",
    ],
    "parameters": {
        "min_composite_score": 7,
        "min_catalyst_score": 5,
        "min_rel_volume": 1.5,
    },
}

# The lab may move parameters only inside these bounds.
PARAM_BOUNDS = {
    "min_composite_score": (6, 9),
    "min_catalyst_score": (4, 8),
    "min_rel_volume": (1.2, 3.0),
}


def load_playbook(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(json.dumps(DEFAULT_PLAYBOOK))


def save_playbook(path: str, playbook: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    playbook["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(playbook, f, indent=2)


def append_journal(journal_path: str, entry: dict) -> None:
    os.makedirs(os.path.dirname(journal_path) or ".", exist_ok=True)
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def load_memory_from_disk(memory, memory_path: str) -> int:
    """Hydrate a FinancialSituationMemory from its JSON snapshot so
    lessons survive across daily processes. Returns pairs loaded."""
    if not os.path.exists(memory_path):
        return 0
    with open(memory_path, "r", encoding="utf-8") as f:
        pairs = json.load(f)
    if pairs:
        memory.add_situations([(p[0], p[1]) for p in pairs])
    return len(pairs)


def append_memory_to_disk(memory_path: str, situation: str, lesson: str) -> None:
    pairs = []
    if os.path.exists(memory_path):
        with open(memory_path, "r", encoding="utf-8") as f:
            pairs = json.load(f)
    pairs.append([situation, lesson])
    os.makedirs(os.path.dirname(memory_path) or ".", exist_ok=True)
    with open(memory_path, "w", encoding="utf-8") as f:
        json.dump(pairs, f, indent=2)


def create_journal_node(journal_path: str):
    """Terminal node of every session run: one structured journal line."""

    def journal_node(state):
        entry = {
            "date": state["trade_date"],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "selected_ticker": state.get("selected_ticker", ""),
            "trade_plan": state.get("trade_plan", ""),
            "risk_verdict": state.get("risk_verdict", ""),
            "execution_report": state.get("execution_report", ""),
            "devils_advocate_verdict": _last_line(
                state.get("devils_advocate_report", ""), "VERDICT:"
            ),
        }
        append_journal(journal_path, entry)
        summary = (
            f"Journaled session {state['trade_date']}: "
            f"ticker={entry['selected_ticker'] or 'NO_TRADE'}"
        )
        return {"journal_entry": summary}

    return journal_node


def _last_line(text: str, prefix: str) -> str:
    for line in reversed(text.strip().splitlines()):
        if prefix in line:
            return line.strip()
    return ""


def create_strategy_lab(
    llm, playbook_path: str, journal_path: str, trader_memory, memory_path: str = None
):
    """Reflection runner (invoked after the close, outside the intraday
    graph): reads recent journal entries + realized P&L and rewrites the
    playbook within bounds. Also feeds the trader memory (persisted to
    memory_path so lessons survive across daily processes)."""

    def reflect(trade_date: str, realized_pnl_note: str = "") -> str:
        playbook = load_playbook(playbook_path)

        recent = []
        if os.path.exists(journal_path):
            with open(journal_path, "r", encoding="utf-8") as f:
                recent = [json.loads(line) for line in f if line.strip()][-15:]

        prompt = f"""{MISSION_BRIEF}

ROLE: Strategy Lab — the desk's nightly research meeting, run after the
close on {trade_date}. You review what the desk actually did and update
the playbook so tomorrow's agents are smarter than today's.

You may change, within reason:
- "watchlist": rotate in liquid names showing sustained momentum or
  upcoming catalysts; rotate out dead ones. Keep 15-25 symbols, all
  liquid US equities/ETFs a small account can trade.
- "setup_stats": update win/loss tallies per setup from the journal.
- "lessons": distill NEW, specific, actionable lessons (max 10 total;
  merge or drop stale ones). "Be careful" is not a lesson; "GAP_AND_GO
  entries before 10:00 ET stopped out 3/3 times — wait for the
  opening-range high" is.
- "parameters": min_composite_score in [6,9], min_catalyst_score in
  [4,8], min_rel_volume in [1.2,3.0]. Tighten after losses; loosen only
  after a statistically meaningful win streak (5+ trades).

You may NOT touch risk limits — those live in the deterministic guard.

Recent journal entries (most recent last):
{json.dumps(recent, indent=2)[:12000]}

Realized P&L / position notes provided by the runner:
{realized_pnl_note or "none provided"}

Current playbook:
{json.dumps(playbook, indent=2)}

Respond with ONLY the complete updated playbook JSON (same schema, no
markdown fences), followed by the line
LESSON_FOR_MEMORY: <one sentence capturing today's most important lesson>"""

        response = llm.invoke(prompt)
        content = response.content

        new_playbook = _extract_playbook(content, playbook)
        save_playbook(playbook_path, new_playbook)

        lesson = _last_line(content, "LESSON_FOR_MEMORY:")
        if lesson and recent:
            situation = json.dumps(recent[-1])[:2000]
            advice = lesson.split(":", 1)[1].strip()
            trader_memory.add_situations([(situation, advice)])
            if memory_path:
                append_memory_to_disk(memory_path, situation, advice)

        return json.dumps(new_playbook, indent=2)

    return reflect


def _extract_playbook(raw: str, fallback: dict) -> dict:
    """Parse the lab's playbook JSON and clamp parameters to bounds."""
    match = re.search(r"\{.*\}", raw.split("LESSON_FOR_MEMORY")[0], re.DOTALL)
    if not match:
        return fallback
    try:
        candidate = json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback
    if not isinstance(candidate, dict) or "watchlist" not in candidate:
        return fallback

    params = candidate.get("parameters", {})
    for key, (lo, hi) in PARAM_BOUNDS.items():
        if key in params:
            try:
                params[key] = min(max(float(params[key]), lo), hi)
            except (TypeError, ValueError):
                params[key] = fallback.get("parameters", {}).get(key, lo)
    candidate["parameters"] = {**fallback.get("parameters", {}), **params}
    candidate["version"] = int(fallback.get("version", 1)) + 1
    return candidate
