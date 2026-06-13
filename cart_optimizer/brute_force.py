"""Brute-force reference solver (test oracle).

Enumerates every possible cart — each product (item or combo) contributes
{skip} ∪ {every valid line: variant × addon selection × quantity} — and every
coupon choice, prices each through the same pricing module the optimizer
uses, and returns the best: max preference, ties broken by lower total.
Exponential in menu size — only for small menus inside tests, never shipped
to users. Its value is being *obviously* correct, so the property tests can
assert the clever DP always matches it.
"""

from __future__ import annotations

import datetime as dt
from itertools import product

from .choices import menu_choices
from .models import Cart, Menu, PricingConfig, User
from .pricing import SolveResult, empty_breakdown, is_eligible, price_cart

__all__ = ["best_cart_brute_force"]


def best_cart_brute_force(
    menu: Menu,
    user: User,
    config: PricingConfig,
    budget: float,
    now: dt.time | str | None = None,
) -> SolveResult:
    if budget < 0:
        raise ValueError("budget must be >= 0")
    products = menu_choices(menu, user, now)
    best = SolveResult(Cart(), None, empty_breakdown())
    best_pref = 0.0
    for picks in product(*[(None, *lines) for lines in products]):
        cart = Cart(tuple(line for line in picks if line is not None))
        for coupon in (None, *menu.coupons):
            if coupon is not None:
                if not cart.lines or not is_eligible(cart, coupon, user):
                    continue
            breakdown = price_cart(cart, coupon, user, config)
            if breakdown.total > budget:
                continue
            pref = sum(line.preference for line in cart.lines)
            if pref > best_pref or (
                pref == best_pref and breakdown.total < best.breakdown.total
            ):
                best = SolveResult(cart, coupon, breakdown)
                best_pref = pref
    return best
