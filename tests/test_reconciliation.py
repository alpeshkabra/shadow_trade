from decimal import Decimal

from shadow_trade import OrderSide
from shadow_trade.brokers.base import Position
from shadow_trade.reconciliation import diff_positions


def test_diff_positions_pure():
    master = {"RELIANCE": Position("RELIANCE", Decimal("100"))}
    client = {"RELIANCE": Position("RELIANCE", Decimal("100"))}
    assert diff_positions(master, client) == {}

    client_short = {"RELIANCE": Position("RELIANCE", Decimal("40"))}
    assert diff_positions(master, client_short) == {"RELIANCE": Decimal("60")}

    # Instrument the client holds but master doesn't.
    extra = {"TCS": Position("TCS", Decimal("10"))}
    assert diff_positions({}, extra) == {"TCS": Decimal("-10")}


def test_matched_book_reconciles_clean(master, make_engine):
    engine, _ = make_engine({"A": {"copy_ratio": Decimal("1")}})
    master.place_order(_buy("RELIANCE", Decimal("100")))
    engine.on_master_trade("ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900"))

    result = engine.reconcile()
    assert result.all_matched is True
    assert result.disabled == 0


def test_drifted_client_is_disabled(master, make_engine):
    engine, brokers = make_engine({"C": {}})
    # Master holds 100; the client's copy was slippage-rejected so it holds 0.
    master.place_order(_buy("RELIANCE", Decimal("100")))
    brokers["C"].set_price("RELIANCE", Decimal("2900") * Decimal("1.03"))
    engine.on_master_trade("ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900"))

    result = engine.reconcile()
    assert result.all_matched is False
    assert result.disabled == 1


def _buy(instrument, qty):
    from shadow_trade import OrderRequest, OrderType

    return OrderRequest(instrument, OrderSide.BUY, qty, OrderType.MARKET)
