"""Shared money math: price a cart under an optional coupon.

Both solvers go through the amount-level functions here — the optimizer's DP
reasons about spend levels without materialized carts, the brute-force oracle
prices real carts — so the two can only ever differ in search strategy, never
in pricing rules.

Final price = subtotal − discount + delivery fee + platform fee + GST, where
GST applies to the discounted item total. ``free_delivery`` waives the
delivery fee instead of discounting items. Totals are rounded to 2 dp; this
model ranks candidate carts — the authoritative bill comes from Swiggy when
the winning cart is built for review.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Cart, Coupon, PricingConfig, User
from .safe_eval import safe_eval

__all__ = [
    "PriceBreakdown",
    "SolveResult",
    "empty_breakdown",
    "is_eligible",
    "is_eligible_amounts",
    "price_amounts",
    "price_cart",
]


@dataclass(frozen=True)
class PriceBreakdown:
    subtotal: float
    discount: float
    delivery_fee: float
    platform_fee: float
    tax: float
    total: float
    coupon_id: str | None = None


@dataclass(frozen=True)
class SolveResult:
    cart: Cart
    coupon: Coupon | None
    breakdown: PriceBreakdown

    @property
    def preference(self) -> float:
        return sum(line.preference for line in self.cart.lines)


def empty_breakdown() -> PriceBreakdown:
    return PriceBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None)


def _query_context(subtotal: float, select_subtotal: float, user: User) -> dict:
    return {
        "subtotal": subtotal,
        "select_subtotal": select_subtotal,
        "user": user.as_context(),
    }


def is_eligible_amounts(
    coupon: Coupon, subtotal: float, select_subtotal: float, user: User
) -> bool:
    """Eligibility from amounts alone. ``select_subtotal`` is the spend on the
    coupon's scope (== subtotal for unscoped coupons)."""
    if subtotal <= 0:
        return False  # coupons never apply to an empty cart
    if not coupon.query:
        return True
    return bool(safe_eval(coupon.query, _query_context(subtotal, select_subtotal, user)))


def is_eligible(cart: Cart, coupon: Coupon, user: User) -> bool:
    subtotal = cart.subtotal
    select = cart.select_subtotal(coupon.applies_to) if coupon.is_scoped else subtotal
    return is_eligible_amounts(coupon, subtotal, select, user)


def _discount_amount(coupon: Coupon, subtotal: float, select_subtotal: float) -> float:
    base = select_subtotal if coupon.is_scoped else subtotal
    if coupon.kind == "flat":
        return float(min(coupon.value, base))
    if coupon.kind == "percent":
        raw = coupon.value * base / 100.0
        if coupon.cap is not None:
            raw = min(raw, coupon.cap)
        return float(min(raw, base))
    return 0.0  # free_delivery waives the fee, not the items


def price_amounts(
    subtotal: float,
    select_subtotal: float,
    coupon: Coupon | None,
    user: User,
    config: PricingConfig,
) -> PriceBreakdown:
    """Price a (subtotal, scope-subtotal) spend pair under an optional coupon.

    Raises ValueError if the coupon is not eligible at these amounts — callers
    are expected to check eligibility first.
    """
    if subtotal <= 0:
        if coupon is not None:
            raise ValueError(f"coupon {coupon.id} cannot apply to an empty cart")
        return empty_breakdown()
    if coupon is not None and not is_eligible_amounts(
        coupon, subtotal, select_subtotal, user
    ):
        raise ValueError(f"coupon {coupon.id} is not eligible at subtotal {subtotal}")
    discount = (
        _discount_amount(coupon, subtotal, select_subtotal) if coupon else 0.0
    )
    delivery = (
        0.0
        if coupon is not None and coupon.kind == "free_delivery"
        else float(config.delivery_fee)
    )
    taxable = subtotal - discount
    tax = round(config.gst_rate * taxable, 2)
    total = round(taxable + delivery + config.platform_fee + tax, 2)
    return PriceBreakdown(
        subtotal=float(subtotal),
        discount=discount,
        delivery_fee=delivery,
        platform_fee=float(config.platform_fee),
        tax=tax,
        total=total,
        coupon_id=coupon.id if coupon else None,
    )


def price_cart(
    cart: Cart, coupon: Coupon | None, user: User, config: PricingConfig
) -> PriceBreakdown:
    subtotal = cart.subtotal
    select = (
        cart.select_subtotal(coupon.applies_to)
        if coupon is not None and coupon.is_scoped
        else subtotal
    )
    return price_amounts(subtotal, select, coupon, user, config)
