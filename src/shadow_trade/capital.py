"""Capital Validator (HLD section 4.3).

Guards against over-exposure on a follower account: checks the account is
enabled, has margin for the notional, and is under its open-trade cap — the
"no capital overexposure on any client account" guarantee.
"""

from dataclasses import dataclass
from decimal import Decimal

from .brokers.base import OrderRequest
from .models import ClientAccount


@dataclass
class CapitalDecision:
    approved: bool
    reason: str = ""


class CapitalValidator:
    def validate(
        self,
        account: ClientAccount,
        order: OrderRequest,
        reference_price: Decimal,
        open_trade_count: int,
    ) -> CapitalDecision:
        if not account.enabled:
            return CapitalDecision(False, "ACCOUNT_DISABLED")

        if open_trade_count >= account.max_open_trades:
            return CapitalDecision(False, "MAX_OPEN_TRADES_EXCEEDED")

        notional = Decimal(order.quantity) * Decimal(reference_price)
        if notional > Decimal(account.available_margin):
            return CapitalDecision(
                False,
                f"INSUFFICIENT_MARGIN(required={notional}, "
                f"available={account.available_margin})",
            )

        return CapitalDecision(True)
