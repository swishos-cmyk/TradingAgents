from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.brokers.paper_broker import PaperBroker


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance_days(self, days):
        self.now += timedelta(days=days)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def broker(tmp_path, clock):
    quotes = {"TEST": 10.0}
    b = PaperBroker(
        state_path=str(tmp_path / "paper.json"),
        starting_cash=1000.0,
        slippage_bps=0.0,
        quote_fn=lambda s: quotes[s],
        now_fn=clock,
    )
    b._quotes = quotes  # test handle for moving prices
    return b


def test_initial_account(broker):
    acct = broker.get_account()
    assert acct.equity == 1000.0
    assert acct.settled_cash == 1000.0
    assert acct.account_type == "cash"


def test_buy_and_position(broker):
    order = broker.place_order("TEST", "buy", 40)
    assert order.status == "filled"
    assert order.filled_price == 10.0

    acct = broker.get_account()
    assert acct.settled_cash == 600.0
    assert acct.equity == 1000.0  # cash + position value

    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 40


def test_insufficient_cash_rejected(broker):
    with pytest.raises(ValueError, match="Insufficient settled cash"):
        broker.place_order("TEST", "buy", 200)  # $2000 > $1000


def test_sell_proceeds_settle_t_plus_1(broker, clock):
    broker.place_order("TEST", "buy", 40)
    broker._quotes["TEST"] = 11.0
    broker.place_order("TEST", "sell", 40)

    acct = broker.get_account()
    assert acct.settled_cash == 600.0        # proceeds not yet settled
    assert acct.cash == pytest.approx(1040.0)  # but counted in total cash

    clock.advance_days(1)
    acct = broker.get_account()
    assert acct.settled_cash == pytest.approx(1040.0)


def test_oversell_rejected(broker):
    broker.place_order("TEST", "buy", 10)
    with pytest.raises(ValueError, match="position too small"):
        broker.place_order("TEST", "sell", 20)


def test_stop_loss_triggers_on_breach(broker):
    broker.place_order("TEST", "buy", 40)
    broker.place_stop_loss("TEST", 40, stop_price=9.5)

    broker._quotes["TEST"] = 9.8
    assert broker.process_stops() == []      # not breached

    broker._quotes["TEST"] = 9.4
    filled = broker.process_stops()
    assert len(filled) == 1
    assert filled[0].side == "sell"
    assert broker.get_positions() == []


def test_state_persists_across_instances(tmp_path, clock):
    quotes = {"TEST": 10.0}
    b1 = PaperBroker(
        state_path=str(tmp_path / "p.json"),
        starting_cash=1000.0,
        slippage_bps=0.0,
        quote_fn=lambda s: quotes[s],
        now_fn=clock,
    )
    b1.place_order("TEST", "buy", 10)

    b2 = PaperBroker(
        state_path=str(tmp_path / "p.json"),
        quote_fn=lambda s: quotes[s],
        now_fn=clock,
    )
    assert b2.get_positions()[0].quantity == 10
    assert b2.get_account().settled_cash == 900.0


def test_slippage_applied(tmp_path, clock):
    quotes = {"TEST": 100.0}
    b = PaperBroker(
        state_path=str(tmp_path / "p.json"),
        starting_cash=1000.0,
        slippage_bps=10.0,  # 0.1%
        quote_fn=lambda s: quotes[s],
        now_fn=clock,
    )
    buy = b.place_order("TEST", "buy", 1)
    assert buy.filled_price == pytest.approx(100.10)
    sell = b.place_order("TEST", "sell", 1)
    assert sell.filled_price == pytest.approx(99.90)
