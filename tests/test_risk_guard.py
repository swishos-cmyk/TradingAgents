import json
import os
import tempfile

import pytest

from tradingagents.brokers.risk_guard import RiskGuard


@pytest.fixture
def guard(tmp_path):
    return RiskGuard(state_path=str(tmp_path / "risk_state.json"))


def make_account(equity=1000.0, settled=1000.0, account_type="cash"):
    return {
        "equity": equity,
        "cash": settled,
        "settled_cash": settled,
        "buying_power": settled,
        "account_type": account_type,
    }


def make_plan(entry=10.0, stop=9.5, target=11.0, symbol="TEST"):
    return {
        "action": "TRADE",
        "symbol": symbol,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
    }


class TestSizing:
    def test_risk_based_size(self, guard):
        sizing = guard.size_position(equity=1000, entry=10.0, stop=9.5)
        # risk budget $20, stop distance $0.50 -> 40 shares = $400 notional
        assert sizing["quantity"] == 40
        assert sizing["notional"] == 400.0
        assert sizing["risk_amount"] == 20.0

    def test_notional_cap_binds(self, guard):
        # tight stop would imply a huge position; the 50% cap must bind
        sizing = guard.size_position(equity=1000, entry=10.0, stop=9.95)
        assert sizing["notional"] <= 500.0 + 1e-6

    def test_zero_when_stop_above_entry(self, guard):
        sizing = guard.size_position(equity=1000, entry=10.0, stop=10.5)
        assert sizing["quantity"] == 0


class TestEvaluate:
    def test_approves_good_plan(self, guard):
        verdict = guard.evaluate(make_plan(), make_account(), on_date="2026-08-03")
        assert verdict.approved
        assert verdict.quantity > 0

    def test_rejects_bad_reward_risk(self, guard):
        verdict = guard.evaluate(
            make_plan(entry=10.0, stop=9.5, target=10.4),
            make_account(),
            on_date="2026-08-03",
        )
        assert not verdict.approved
        assert any("Reward:risk" in r for r in verdict.reasons)

    def test_rejects_missing_stop(self, guard):
        plan = make_plan()
        plan["stop_loss"] = 0
        verdict = guard.evaluate(plan, make_account(), on_date="2026-08-03")
        assert not verdict.approved

    def test_rejects_penny_stock(self, guard):
        verdict = guard.evaluate(
            make_plan(entry=0.50, stop=0.40, target=0.80),
            make_account(),
            on_date="2026-08-03",
        )
        assert not verdict.approved

    def test_rejects_at_max_positions(self, guard):
        verdict = guard.evaluate(
            make_plan(), make_account(), open_positions=2, on_date="2026-08-03"
        )
        assert not verdict.approved

    def test_settled_cash_shrinks_size(self, guard):
        verdict = guard.evaluate(
            make_plan(),
            make_account(equity=1000, settled=100),
            on_date="2026-08-03",
        )
        assert verdict.approved
        assert verdict.notional <= 100


class TestCircuitBreakers:
    def test_daily_loss_halt(self, guard):
        guard.check_account_health(1000.0, on_date="2026-08-03")
        verdict = guard.evaluate(
            make_plan(), make_account(equity=940), on_date="2026-08-03"
        )
        assert not verdict.approved
        assert verdict.halted

    def test_kill_switch_engages_and_persists(self, tmp_path):
        path = str(tmp_path / "risk_state.json")
        guard = RiskGuard(state_path=path)
        guard.check_account_health(1000.0, on_date="2026-08-01")
        halts = guard.check_account_health(790.0, on_date="2026-08-05")
        assert any("KILL SWITCH" in h for h in halts)

        # fresh instance reads the same state file: still halted
        guard2 = RiskGuard(state_path=path)
        verdict = guard2.evaluate(
            make_plan(), make_account(equity=790), on_date="2026-08-06"
        )
        assert verdict.halted

        guard2.reset_kill_switch()
        verdict = guard2.evaluate(
            make_plan(), make_account(equity=790), on_date="2026-08-06"
        )
        assert not verdict.halted

    def test_pdt_blocks_fourth_day_trade(self, guard):
        for i in range(3):
            guard.record_day_trade("TEST", on_date="2026-08-03")
        verdict = guard.evaluate(
            {**make_plan(), "max_holding_days": 0},
            make_account(account_type="margin"),
            is_day_trade=True,
            on_date="2026-08-04",
        )
        assert not verdict.approved
        assert any("PDT" in r for r in verdict.reasons)

    def test_pdt_ignored_for_cash_account(self, guard):
        for i in range(3):
            guard.record_day_trade("TEST", on_date="2026-08-03")
        verdict = guard.evaluate(
            {**make_plan(), "max_holding_days": 0},
            make_account(account_type="cash"),
            is_day_trade=True,
            on_date="2026-08-04",
        )
        assert verdict.approved
