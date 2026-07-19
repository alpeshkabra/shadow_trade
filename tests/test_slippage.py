from decimal import Decimal

from shadow_trade.slippage import SlippageGuard, slippage_pct


def test_slippage_pct_basic():
    assert slippage_pct(Decimal("100"), Decimal("102")) == Decimal("2")
    assert slippage_pct(Decimal("100"), Decimal("98")) == Decimal("2")
    assert slippage_pct(Decimal("100"), Decimal("100")) == Decimal("0")


def test_slippage_pct_zero_master_price_is_safe():
    assert slippage_pct(Decimal("0"), Decimal("100")) == Decimal("0")


def test_guard_allows_within_threshold():
    guard = SlippageGuard(Decimal("2.0"))
    allowed, pct = guard.evaluate(Decimal("2900"), Decimal("2950"))  # ~1.72%
    assert allowed is True
    assert pct < Decimal("2.0")


def test_guard_rejects_beyond_threshold():
    guard = SlippageGuard(Decimal("2.0"))
    allowed, pct = guard.evaluate(Decimal("2900"), Decimal("2987"))  # 3%
    assert allowed is False
    assert pct == Decimal("3")


def test_guard_boundary_is_inclusive():
    guard = SlippageGuard(Decimal("2.0"))
    allowed, pct = guard.evaluate(Decimal("100"), Decimal("102"))  # exactly 2%
    assert allowed is True
    assert pct == Decimal("2")
