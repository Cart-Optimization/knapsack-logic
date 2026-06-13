"""Coupon-aware exact optimizer.

Products (items and combos) are first expanded into their valid order lines
(variant × addon selection × quantity — see choices.py); the solver picks at
most one line per product, which is a multiple-choice knapsack. The DP runs
over *exact* spend levels, then a coupon layer:

* One full-menu DP serves the no-coupon case and every unscoped coupon — at
  spend ``s`` the discount and eligibility depend only on ``s`` (and the
  user), so evaluating each coupon at each reachable spend is exhaustive.
  This is what captures the FLAT100 step function: a ₹210 cart can price
  below a ₹150 one after crossing the ₹199 threshold.
* Each scoped coupon (``applies_to``) gets a two-knapsack decomposition:
  one DP over in-scope products, one over the rest. Preference is additive
  and the price depends only on the pair (scope spend, rest spend), so
  pricing every reachable pair is exhaustive without materializing carts.

Money math and eligibility go through pricing.price_amounts — the same code
path the brute-force oracle uses — so the solvers can only differ in search
strategy, never in pricing rules. Returns the provably best cart: maximum
total preference, ties broken by lower total price.
"""

from __future__ import annotations

import datetime as dt

from .choices import menu_choices
from .models import Cart, ComboLine, Coupon, ItemLine, Menu, PricingConfig, User
from .pricing import (
    SolveResult,
    empty_breakdown,
    is_eligible_amounts,
    price_amounts,
    price_cart,
)

__all__ = ["best_cart"]

Line = ItemLine | ComboLine

NEG = float("-inf")

# Slack for the dp-level pre-check only: a scoped cart's preference is
# re-summed in menu order inside `consider`, which can differ from the
# dpA+dpB float by ~1e-16. The pre-check must not reject such borderline
# winners; `consider` re-compares exactly (same comparator as brute force).
_PRECHECK_SLACK = 1e-12


def best_cart(
    menu: Menu,
    user: User,
    config: PricingConfig,
    budget: float,
    now: dt.time | str | None = None,
) -> SolveResult:
    """Best cart for this menu/user/budget: max preference, then min total,
    final price (after best coupon + fees + GST) within budget."""
    if budget < 0:
        raise ValueError("budget must be >= 0")
    products = menu_choices(menu, user, now)

    best = SolveResult(Cart(), None, empty_breakdown())  # empty cart is always feasible
    best_pref = 0.0

    def consider(cart: Cart, coupon: Coupon | None) -> None:
        nonlocal best, best_pref
        breakdown = price_cart(cart, coupon, user, config)
        if breakdown.total > budget:
            return
        pref = sum(line.preference for line in cart.lines)
        if pref > best_pref or (
            pref == best_pref and breakdown.total < best.breakdown.total
        ):
            best = SolveResult(cart, coupon, breakdown)
            best_pref = pref

    max_spend = _max_spend(products, menu.coupons, budget)

    # --- no coupon + every unscoped coupon: one DP, evaluated per spend level
    dp, choice_rows = _knapsack(products, max_spend)
    unscoped = [coupon for coupon in menu.coupons if not coupon.is_scoped]
    for spend in range(max_spend + 1):
        if dp[spend] == NEG:
            continue
        for coupon in (None, *unscoped):
            if spend == 0:
                if coupon is not None:
                    continue  # coupons never apply to an empty cart
            elif coupon is not None and not is_eligible_amounts(
                coupon, spend, spend, user
            ):
                continue
            breakdown = price_amounts(spend, spend, coupon, user, config)
            if breakdown.total > budget:
                continue
            if dp[spend] + _PRECHECK_SLACK > best_pref or (
                dp[spend] >= best_pref - _PRECHECK_SLACK
                and breakdown.total < best.breakdown.total
            ):
                consider(Cart(tuple(_backtrack(products, choice_rows, spend))), coupon)

    # --- scoped coupons: in-scope knapsack × rest knapsack
    order = {lines[0].product_id: index for index, lines in enumerate(products)}
    for coupon in menu.coupons:
        if not coupon.is_scoped:
            continue
        in_scope = [lines for lines in products if lines[0].product_id in coupon.applies_to]
        rest = [lines for lines in products if lines[0].product_id not in coupon.applies_to]
        dp_scope, rows_scope = _knapsack(in_scope, max_spend)
        dp_rest, rows_rest = _knapsack(rest, max_spend)
        reachable_rest = [b for b in range(max_spend + 1) if dp_rest[b] != NEG]
        for scope_spend in range(max_spend + 1):
            if dp_scope[scope_spend] == NEG:
                continue
            for rest_spend in reachable_rest:
                subtotal = scope_spend + rest_spend
                if subtotal > max_spend:
                    break  # reachable_rest is ascending
                if subtotal == 0:
                    continue
                if not is_eligible_amounts(coupon, subtotal, scope_spend, user):
                    continue
                breakdown = price_amounts(subtotal, scope_spend, coupon, user, config)
                if breakdown.total > budget:
                    continue
                pref = dp_scope[scope_spend] + dp_rest[rest_spend]
                if pref + _PRECHECK_SLACK > best_pref or (
                    pref >= best_pref - _PRECHECK_SLACK
                    and breakdown.total < best.breakdown.total
                ):
                    lines = _backtrack(in_scope, rows_scope, scope_spend) + _backtrack(
                        rest, rows_rest, rest_spend
                    )
                    cart = Cart(
                        tuple(sorted(lines, key=lambda line: order[line.product_id]))
                    )
                    consider(cart, coupon)

    return best


def _max_spend(
    products: list[list[Line]], coupons: tuple[Coupon, ...], budget: float
) -> int:
    """Largest subtotal worth exploring. total >= subtotal - discount, so a
    subtotal is only feasible if some coupon can claw it back under budget."""
    menu_max = sum(max(line.cost for line in lines) for lines in products)
    headroom = 0.0
    for coupon in coupons:
        if coupon.kind == "flat":
            headroom = max(headroom, coupon.value)
        elif coupon.kind == "percent":
            if coupon.cap is not None:
                headroom = max(headroom, coupon.cap)
            elif coupon.value >= 100:
                return menu_max  # everything could be free
            else:
                fraction = coupon.value / 100.0
                headroom = max(headroom, budget * fraction / (1.0 - fraction))
    return min(menu_max, int(budget + headroom))


def _knapsack(
    products: list[list[Line]], max_spend: int
) -> tuple[list[float], list[list[int]]]:
    """Multiple-choice knapsack over exact spend levels.

    Returns (dp, rows): dp[s] is the best total preference achievable at
    exactly spend s (NEG if unreachable); rows[i][s] is the index of the
    line taken for product i when the first i+1 products land on spend s,
    or -1 for skip.
    """
    dp = [NEG] * (max_spend + 1)
    dp[0] = 0.0
    rows: list[list[int]] = []
    for lines in products:
        row = [-1] * (max_spend + 1)
        next_dp = dp[:]  # default: skip this product
        for line_index, line in enumerate(lines):
            cost = line.cost
            if cost > max_spend:
                continue
            preference = line.preference
            for spend in range(cost, max_spend + 1):
                base = dp[spend - cost]
                if base == NEG:
                    continue
                candidate = base + preference
                if candidate > next_dp[spend]:
                    next_dp[spend] = candidate
                    row[spend] = line_index
        dp = next_dp
        rows.append(row)
    return dp, rows


def _backtrack(
    products: list[list[Line]], rows: list[list[int]], spend: int
) -> list[Line]:
    """Recover the chosen lines for a reachable spend, in product order."""
    chosen: list[Line] = []
    remaining = spend
    for index in range(len(products) - 1, -1, -1):
        line_index = rows[index][remaining]
        if line_index >= 0:
            line = products[index][line_index]
            chosen.append(line)
            remaining -= line.cost
    assert remaining == 0, "DP bookkeeping is inconsistent"
    chosen.reverse()
    return chosen
