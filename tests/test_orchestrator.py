from decimal import Decimal

from shadow_trade import OrderSide
from shadow_trade.enums import ExecutionStatus


def _by_id(outcomes):
    return {o.client_account_id: o for o in outcomes}


def test_copies_to_all_eligible_clients(make_engine):
    engine, _ = make_engine({
        "A": {"copy_ratio": Decimal("1")},
        "B": {"copy_ratio": Decimal("2")},
    })
    outcomes = _by_id(engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    ))
    assert outcomes["A"].status == ExecutionStatus.FILLED
    assert outcomes["B"].status == ExecutionStatus.FILLED


def test_copy_ratio_scales_quantity(make_engine):
    engine, brokers = make_engine({"B": {"copy_ratio": Decimal("2")}})
    engine.on_master_trade("ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900"))
    # Follower B copies at 2x -> 200 shares on its book.
    assert brokers["B"].get_positions()["RELIANCE"].net_quantity == Decimal("200")


def test_slippage_rejection(make_engine):
    engine, brokers = make_engine({"C": {}})
    # Quote follower C's market 3% above the master fill -> must be rejected.
    brokers["C"].set_price("RELIANCE", Decimal("2900") * Decimal("1.03"))
    outcomes = _by_id(engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    ))
    assert outcomes["C"].status == ExecutionStatus.SLIPPAGE_REJECTED
    assert "RELIANCE" not in brokers["C"].get_positions()


def test_insufficient_margin_rejected(make_engine):
    engine, _ = make_engine({"A": {"margin": Decimal("1000")}})  # far too little
    outcomes = _by_id(engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    ))
    assert outcomes["A"].status == ExecutionStatus.REJECTED
    assert "INSUFFICIENT_MARGIN" in outcomes["A"].detail


def test_kill_switch_drops_event(make_engine):
    engine, brokers = make_engine({"A": {}})
    engine.kill_switch.activate("maintenance")
    outcomes = engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    )
    assert outcomes == []
    assert brokers["A"].get_positions() == {}


def test_retry_then_success(make_engine):
    engine, brokers = make_engine({"A": {}})
    brokers["A"].fail_next_orders(2)  # two transient failures, then succeeds
    outcomes = _by_id(engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    ))
    assert outcomes["A"].status == ExecutionStatus.FILLED
