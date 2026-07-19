"""End-to-end demo of the ShadowTrade Copy Trading Engine.

Runs entirely in-memory against simulated brokers so you can see the full
flow without a real broker or PostgreSQL:

    master places trades  ->  engine copies to 3 followers  ->  reconcile

Run:  python run_demo.py
"""

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from shadow_trade import CopyTradingEngine, OrderSide, Settings, SimulatedBroker  # noqa: E402

PRICES = {"RELIANCE": Decimal("2900"), "TCS": Decimal("3850"), "INFY": Decimal("1650")}


def main() -> None:
    # Fresh in-memory DB for a clean demo run.
    settings = Settings(database_url="sqlite:///:memory:")

    master = SimulatedBroker("MASTER", PRICES)

    # Three followers. FOLLOWER_C's market is quoted 3% away from the master to
    # trip the 2% slippage guard on purpose.
    followers = {
        "FOLLOWER_A": SimulatedBroker("A", PRICES),
        "FOLLOWER_B": SimulatedBroker("B", PRICES),
        "FOLLOWER_C": SimulatedBroker("C", dict(PRICES)),
    }
    followers["FOLLOWER_C"].set_price("RELIANCE", Decimal("2900") * Decimal("1.03"))

    engine = CopyTradingEngine(master, followers, settings=settings)
    engine.register_client("FOLLOWER_A", "Alice", Decimal("1000000"), copy_ratio=Decimal("1"))
    engine.register_client("FOLLOWER_B", "Bob", Decimal("1000000"), copy_ratio=Decimal("2"))
    engine.register_client("FOLLOWER_C", "Carol", Decimal("1000000"))

    print("=== Master BUYs 100 RELIANCE @ 2900 ===")
    # The master's own fill happens at the real broker; here we reflect it on
    # the simulated master book so reconciliation has something to compare.
    master.place_order(_req("RELIANCE", OrderSide.BUY, Decimal("100")))
    outcomes = engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    )
    for o in outcomes:
        print(f"  {o.client_account_id:12} -> {o.status.value:18} "
              f"slippage={o.slippage_pct:.2f}%  {o.detail}")

    print("\n=== Master BUYs 50 TCS @ 3850 ===")
    master.place_order(_req("TCS", OrderSide.BUY, Decimal("50")))
    for o in engine.on_master_trade(
        "ORD-2", "TCS", OrderSide.BUY, Decimal("50"), Decimal("3850")
    ):
        print(f"  {o.client_account_id:12} -> {o.status.value:18} {o.detail}")

    print("\n=== Idempotency: replay ORD-1 (should all skip) ===")
    for o in engine.on_master_trade(
        "ORD-1", "RELIANCE", OrderSide.BUY, Decimal("100"), Decimal("2900")
    ):
        print(f"  {o.client_account_id:12} -> {o.status.value}")

    print("\n=== Reconciliation (disable-on-drift mode) ===")
    result = engine.reconcile()
    print(f"  checked={result.checked_clients} mismatches={len(result.mismatches)} "
          f"disabled={result.disabled} all_matched={result.all_matched}")
    for m in result.mismatches:
        print(f"    mismatch {m.client_account_id} {m.instrument}: "
              f"master={m.master_qty} client={m.client_qty}")


def _req(instrument, side, qty):
    from shadow_trade import OrderRequest, OrderType

    return OrderRequest(instrument, side, qty, OrderType.MARKET)


if __name__ == "__main__":
    main()
