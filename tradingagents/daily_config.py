# TradingAgents/daily_config.py
"""Configuration for the Claude-powered daily trading graph.

Everything defaults to SAFE: paper broker, small risk limits, yfinance
data. Live Robinhood execution requires flipping execution_mode AND
setting the TRADINGAGENTS_LIVE_TRADING acknowledgment env var.
"""

from tradingagents.default_config import DEFAULT_CONFIG

DAILY_TRADING_CONFIG = {
    **DEFAULT_CONFIG,
    # --- Claude everywhere -------------------------------------------
    "llm_provider": "anthropic",
    "deep_think_llm": "claude-opus-4-6",     # devil's advocate, planner, risk officer, strategy lab
    "quick_think_llm": "claude-sonnet-4-6",  # tool-loop analysts (scanner, setup, catalyst)
    "backend_url": None,
    "anthropic_effort": "high",
    # --- Broker / execution -------------------------------------------
    "broker": "robinhood",          # used only when execution_mode == "live"
    "execution_mode": "paper",      # "paper" (default) or "live"
    "starting_cash": 1000.0,
    "paper_slippage_bps": 10.0,
    # --- Deterministic risk limits (RiskGuard) -------------------------
    "risk_params": {
        "max_risk_per_trade_pct": 0.02,
        "max_position_pct": 0.50,
        "max_open_positions": 2,
        "min_reward_risk": 2.0,
        "daily_loss_halt_pct": 0.05,
        "kill_switch_drawdown_pct": 0.20,
        "min_price": 1.00,
        "max_price": 400.00,
    },
}
