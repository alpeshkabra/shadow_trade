"""Slippage Guard (HLD section 4.3).

Rejects a client execution when the current market price has deviated beyond
the configured threshold (default 2%) from the master's fill price — the core
"no execution beyond 2% price deviation from master" guarantee.
"""

from decimal import Decimal


def slippage_pct(master_price: Decimal, client_price: Decimal) -> Decimal:
    """Absolute percentage deviation of client price from master price."""
    if master_price is None or master_price == 0:
        return Decimal("0")
    return (abs(client_price - master_price) / master_price) * Decimal("100")


class SlippageGuard:
    def __init__(self, max_pct: Decimal):
        self.max_pct = Decimal(max_pct)

    def evaluate(self, master_price: Decimal, client_price: Decimal):
        """Return ``(allowed: bool, pct: Decimal)``."""
        pct = slippage_pct(master_price, client_price)
        return pct <= self.max_pct, pct
