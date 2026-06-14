"""Discovery-and-verify loop: turn the optimizer's estimates into a
Swiggy-confirmed best-value cart.

Why this exists: coupons in Swiggy are hidden until a qualifying cart exists,
the best coupon depends on the cart's contents, and our fee/coupon model is
only an estimate. So we cannot trust a single estimated "best cart". Instead:

1. ``propose_candidates`` generates a *diverse* set of plausible carts
   (anchored on different items) so they trigger different possible coupons.
2. Each candidate is confirmed through a ``CartVerifier`` — in production the
   live Swiggy cart (build → explicitly apply each candidate coupon → read
   bill → flush) and returns the authoritative ``to_pay``. NOTE (verified live
   2026-06-13): Swiggy does NOT auto-apply coupons — the cart only *suggests*
   one (``coupon_discount == 0``); a coupon must be applied explicitly, and it
   can be rejected by item restrictions (SWIGGYIT: "not applicable on
   pre-packaged & combo items"). So which coupon a cart can use is itself
   something only the live cart can tell us.
3. ``discover_best_cart`` keeps the highest-preference candidate whose REAL
   bill is within budget, ties broken by the lower real price.

The verifier is an injected boundary so the whole loop is unit-tested offline
with a fake; the live one lives in ``adapters.swiggy_session`` and is only run
with explicit user approval (it mutates the live cart).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Protocol, Sequence

from .adapters.swiggy import CartBill
from .models import Cart, ItemLine, Menu, PricingConfig, User
from .optimizer import best_cart

__all__ = [
    "CartVerifier",
    "VerifiedCart",
    "discover_best_cart",
    "propose_candidates",
    "scale_menu_costs",
]


def scale_menu_costs(menu: Menu, factor: float) -> Menu:
    """Return a copy of the menu with every price multiplied by ``factor``.

    Swiggy charges item-level-discounted prices (e.g. Taco Bell "ITEMS AT ₹29"),
    so the list prices in the menu over-state the real cost. After one live cart
    reveals the real-vs-listed ratio, we scale the whole menu by it and re-optimize
    so the cart fills the *real* budget instead of stopping early."""
    f = max(0.01, factor)

    def sv(v):
        return dataclasses.replace(v, cost=max(0, round(v.cost * f)))

    def so(o):
        return dataclasses.replace(o, cost=max(0, round(o.cost * f)))

    def sg(g):
        return dataclasses.replace(g, options=tuple(so(o) for o in g.options))

    def si(i):
        return dataclasses.replace(
            i, variants=tuple(sv(v) for v in i.variants),
            addons=tuple(sg(g) for g in i.addons),
        )

    items = tuple(si(i) for i in menu.items)
    combos = tuple(dataclasses.replace(c, cost=max(0, round(c.cost * f)))
                   for c in menu.combos)
    return dataclasses.replace(menu, items=items, combos=combos)


class CartVerifier(Protocol):
    """Anything that can confirm a cart against Swiggy's real bill."""

    def verify(self, cart: Cart) -> CartBill: ...


@dataclass(frozen=True)
class VerifiedCart:
    cart: Cart
    bill: CartBill

    @property
    def preference(self) -> float:
        return sum(line.preference for line in self.cart.lines)


def _cart_key(cart: Cart):
    return frozenset(
        (line.product_id, getattr(line, "variant", None) and line.variant.id, line.quantity)
        for line in cart.lines
    )


def discover_best_cart(
    candidates: Sequence[Cart], verifier: CartVerifier, budget: float
) -> VerifiedCart | None:
    """Verify each candidate against the real bill, return the best within
    budget (max preference, then lowest real ``to_pay``), or None if none fit.

    Duplicate candidate carts are verified only once."""
    verified: list[VerifiedCart] = []
    seen: set = set()
    for cart in candidates:
        if not cart.lines:
            continue
        key = _cart_key(cart)
        if key in seen:
            continue
        seen.add(key)
        verified.append(VerifiedCart(cart, verifier.verify(cart)))

    feasible = [v for v in verified if v.bill.to_pay <= budget + 1e-9]
    if not feasible:
        return None
    return max(feasible, key=lambda v: (v.preference, -v.bill.to_pay))


def propose_candidates(
    menu: Menu,
    user: User,
    config: PricingConfig,
    budget: float,
    max_candidates: int = 8,
) -> list[Cart]:
    """A diverse set of candidate carts to probe different coupons.

    Strategy:
    1. Best estimated cart at a RANGE of budget fractions (1.0, 0.8, 0.65, 0.5).
       This is critical: real Swiggy fees (taxes + packaging, observed ~₹60 fixed
       + ~4% at McDonald's) routinely exceed our PricingConfig estimate, so the
       estimated "best" cart at full budget can bust the *real* budget. Probing
       cheaper sub-budgets guarantees the candidate set includes carts that still
       fit once the authoritative bill comes back — without them the discovery
       loop can only choose among carts that are all over budget.
    2. Carts anchored on each high-*preference* item — taste-driven picks.
    3. Carts anchored on each high-*cost* item — triggers price-threshold coupons
       like SWIGGYIT (only applies on premium items); the cheap preference-max
       cart may be coupon-ineligible.

    Estimates here only choose *which* carts to probe; the real bill comes from
    the verifier and is authoritative.
    """
    candidates: list[Cart] = []
    seen: set = set()

    def add(cart: Cart) -> None:
        if not cart.lines:
            return
        key = _cart_key(cart)
        if key not in seen:
            seen.add(key)
            candidates.append(cart)

    def anchor_on(item) -> None:
        if len(candidates) >= max_candidates:
            return
        if not item.is_orderable():
            return
        anchor = ItemLine(item, min(item.variants, key=lambda v: v.cost))
        if anchor.cost > budget:
            return
        rest_menu = dataclasses.replace(
            menu, items=tuple(i for i in menu.items if i.id != item.id)
        )
        complement = best_cart(rest_menu, user, config, budget - anchor.cost).cart
        add(Cart((anchor,) + complement.lines))

    # 1. Best estimate across a range of budget fractions (fee-misestimation guard).
    for fraction in (1.0, 0.8, 0.65, 0.5):
        if len(candidates) >= max_candidates:
            break
        add(best_cart(menu, user, config, budget * fraction).cart)

    # 2. Anchor on high-preference items.
    for item in sorted(menu.items, key=lambda i: i.preference, reverse=True):
        if len(candidates) >= max_candidates:
            break
        anchor_on(item)

    # 3. Anchor on high-cost items (different set — triggers threshold coupons).
    orderable = [i for i in menu.items if i.is_orderable()]
    for item in sorted(orderable, key=lambda i: max(v.cost for v in i.variants), reverse=True):
        if len(candidates) >= max_candidates:
            break
        anchor_on(item)

    return candidates[:max_candidates]
