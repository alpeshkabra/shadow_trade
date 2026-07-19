import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from shadow_trade import CopyTradingEngine, Settings, SimulatedBroker  # noqa: E402

PRICES = {"RELIANCE": Decimal("2900"), "TCS": Decimal("3850")}


@pytest.fixture
def prices():
    return dict(PRICES)


@pytest.fixture
def master(prices):
    return SimulatedBroker("MASTER", prices)


@pytest.fixture
def make_engine(master, prices):
    """Factory: build an engine with N identical followers on a fresh in-memory DB."""

    def _make(followers):
        settings = Settings(database_url="sqlite:///:memory:")
        brokers = {cid: SimulatedBroker(cid, dict(prices)) for cid in followers}
        engine = CopyTradingEngine(master, brokers, settings=settings)
        for cid, cfg in followers.items():
            engine.register_client(
                cid, cfg.get("name", cid),
                cfg.get("margin", Decimal("10000000")),
                copy_ratio=cfg.get("copy_ratio", Decimal("1")),
                max_open_trades=cfg.get("max_open_trades", 100),
            )
        return engine, brokers

    return _make
