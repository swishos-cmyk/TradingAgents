from .base_broker import Account, BaseBroker, Order, Position
from .paper_broker import PaperBroker
from .risk_guard import DEFAULT_RISK_PARAMS, RiskGuard, RiskVerdict


def create_broker(config: dict) -> BaseBroker:
    """Build the broker selected by config.

    execution_mode "live" + broker "robinhood" returns the Robinhood
    connector (double-gated by env acknowledgment); everything else gets
    the file-backed paper broker.
    """
    import os

    mode = config.get("execution_mode", "paper")
    if mode == "live" and config.get("broker", "robinhood") == "robinhood":
        from .robinhood_broker import RobinhoodBroker

        return RobinhoodBroker(execution_mode=mode)

    state_dir = os.path.join(
        config.get("results_dir", "./results"), "daily_trading"
    )
    return PaperBroker(
        state_path=os.path.join(state_dir, "paper_account.json"),
        starting_cash=config.get("starting_cash", 1000.0),
        slippage_bps=config.get("paper_slippage_bps", 10.0),
    )


__all__ = [
    "Account",
    "BaseBroker",
    "Order",
    "Position",
    "PaperBroker",
    "RiskGuard",
    "RiskVerdict",
    "DEFAULT_RISK_PARAMS",
    "create_broker",
]
