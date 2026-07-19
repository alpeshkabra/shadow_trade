from decimal import Decimal

from shadow_trade import OrderSide
from shadow_trade.enums import ExecutionStatus


def test_replayed_event_is_skipped(make_engine):
    engine, brokers = make_engine({"A": {}})
    first = engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    )
    assert first[0].status == ExecutionStatus.FILLED
    qty_after_first = brokers["A"].get_positions()["RELIANCE"].net_quantity

    # Replaying the exact same master event must NOT double the position.
    replay = engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    )
    assert replay[0].status == ExecutionStatus.SKIPPED_DUPLICATE
    assert brokers["A"].get_positions()["RELIANCE"].net_quantity == qty_after_first
