from tradingagents.brokers import trade_ledger


def test_open_and_close_round_trip(tmp_path):
    path = str(tmp_path / "ledger.json")
    trade_ledger.open_trade(
        path, symbol="TEST", quantity=10, entry_date="2026-08-03",
        entry_price=10.0, stop_loss=9.5, take_profit=11.0, max_holding_days=2,
    )
    assert len(trade_ledger.open_trades(path)) == 1

    closed = trade_ledger.close_trade(
        path, "TEST", exit_date="2026-08-04", exit_price=11.1, reason="take_profit"
    )
    assert closed["entry_date"] == "2026-08-03"
    assert closed["exit_reason"] == "take_profit"
    assert trade_ledger.open_trades(path) == []


def test_close_missing_symbol_returns_none(tmp_path):
    path = str(tmp_path / "ledger.json")
    assert trade_ledger.close_trade(path, "NOPE", "2026-08-04", 1.0, "stop") is None


def test_close_targets_oldest_open_trade(tmp_path):
    path = str(tmp_path / "ledger.json")
    trade_ledger.open_trade(path, "TEST", 10, "2026-08-03", 10.0, 9.5, 11.0, 2)
    trade_ledger.open_trade(path, "TEST", 5, "2026-08-04", 10.5, 10.0, 12.0, 2)
    closed = trade_ledger.close_trade(path, "TEST", "2026-08-05", 11.0, "stop_loss")
    assert closed["quantity"] == 10
    remaining = trade_ledger.open_trades(path)
    assert len(remaining) == 1 and remaining[0]["quantity"] == 5
