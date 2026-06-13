"""Shared test utilities: result validation + random scenario generator.

The generator deliberately exercises the full v2 surface — variants, addon
groups (min/max), per-line quantities, and combos with user-status
applicability — because the equivalence suite is what proves the DP stays
exact once those features expand the search space. Preferences are drawn
from a 0.05 grid (item, addon, and combo alike) so equal carts compare equal
and unequal carts differ by >= 0.05, keeping the abs_tol=1e-9 assertions
unambiguous. Menu complexity is trimmed so the brute-force oracle stays
tractable.
"""

from __future__ import annotations

import random

from cart_optimizer.choices import product_lines
from cart_optimizer.models import (
    AddonGroup,
    AddonOption,
    Combo,
    ComboLine,
    Coupon,
    Item,
    ItemLine,
    Menu,
    PricingConfig,
    User,
    Variant,
)
from cart_optimizer.pricing import is_eligible, price_cart

# Keep the oracle's enumeration (product over products of 1 + #lines) bounded.
BRUTE_FORCE_CART_CAP = 60_000


def assert_valid_result(result, menu, user, config, budget):
    """Structural validity of a solver result, independent of optimality.

    Lines self-validate at construction (addon min/max, quantity bounds), so
    here we check the result-level invariants: every line belongs to the menu
    and is orderable, the claimed coupon is eligible, the reported breakdown
    re-prices exactly, and the total is within budget.
    """
    cart = result.cart
    product_ids = [line.product_id for line in cart.lines]
    assert len(product_ids) == len(set(product_ids)), "duplicate products in cart"
    items = {item.id: item for item in menu.items}
    combos = {combo.id: combo for combo in menu.combos}
    for line in cart.lines:
        if isinstance(line, ItemLine):
            item = items.get(line.item.id)
            assert item is not None and item is line.item, "unknown item in cart"
            assert item.is_orderable(), "unavailable/off-hours item in cart"
            assert any(v.id == line.variant.id for v in item.variants), "bad variant"
            owner = {o.id: g.id for g in item.addons for o in g.options}
            assert all(o.id in owner for o in line.addons), "foreign addon in cart"
        elif isinstance(line, ComboLine):
            combo = combos.get(line.combo.id)
            assert combo is not None and combo is line.combo, "unknown combo in cart"
            assert combo.is_orderable(user), "combo not applicable to this user"
        else:  # pragma: no cover - guards against a new line type slipping through
            raise AssertionError(f"unexpected line type {type(line).__name__}")
    if result.coupon is not None:
        assert is_eligible(cart, result.coupon, user), "claimed coupon not eligible"
    breakdown = price_cart(cart, result.coupon, user, config)
    assert breakdown == result.breakdown, "reported breakdown does not re-price"
    assert breakdown.total <= budget + 1e-9, "over budget"


def _grid(rng: random.Random, lo: int, hi: int) -> float:
    return rng.randint(lo, hi) * 0.05


def _random_item(rng: random.Random, index: int) -> Item:
    variants = tuple(
        Variant(id=f"var_{index}_{v}", name=f"v{v}", cost=rng.randrange(20, 401))
        for v in range(rng.randint(1, 2))
    )
    addons: tuple[AddonGroup, ...] = ()
    if rng.random() < 0.35:  # one optional/mandatory addon group
        n_options = rng.randint(2, 3)
        options = tuple(
            AddonOption(
                id=f"opt_{index}_{o}",
                name=f"o{o}",
                cost=rng.randrange(0, 121),
                preference=_grid(rng, 0, 4),
            )
            for o in range(n_options)
        )
        min_select = rng.randint(0, 1)
        max_select = rng.randint(max(min_select, 1), min(2, n_options))
        addons = (
            AddonGroup(
                id=f"grp_{index}",
                name=f"g{index}",
                min_select=min_select,
                max_select=max_select,
                options=options,
            ),
        )
    return Item(
        id=f"itm_{index}",
        name=f"item {index}",
        preference=_grid(rng, 1, 20),
        variants=variants,
        available=rng.random() > 0.1,
        addons=addons,
        max_quantity=rng.choice([1, 1, 1, 2]),
    )


def _random_combo(rng: random.Random, index: int) -> Combo:
    return Combo(
        id=f"cmb_{index}",
        name=f"combo {index}",
        cost=rng.randrange(80, 401),
        preference=_grid(rng, 1, 20),
        applicability=rng.choice(
            [None, None, None, "user.member == true", "user.first_order == true"]
        ),
        available=rng.random() > 0.1,
        max_quantity=rng.choice([1, 1, 2]),
    )


def _brute_force_size(items, combos) -> int:
    size = 1
    for product in (*items, *combos):
        size *= 1 + len(product_lines(product))
        if size > 10**12:  # avoid unbounded growth while multiplying
            break
    return size


def random_scenario(rng: random.Random):
    """A small random menu/user/config/budget covering the full v2 surface."""
    items = [_random_item(rng, i) for i in range(rng.randint(1, 3))]
    combos = [_random_combo(rng, c) for c in range(rng.randint(0, 2))]

    # Trim trailing products until the oracle's enumeration is tractable.
    while (items or combos) and _brute_force_size(items, combos) > BRUTE_FORCE_CART_CAP:
        if combos:
            combos.pop()
        else:
            items.pop()

    product_ids = [item.id for item in items] + [combo.id for combo in combos]
    coupons = []
    for c in range(rng.randint(0, 3)):
        kind = rng.choice(["flat", "percent", "free_delivery"])
        coupon_id = f"off_{c}"
        if kind == "flat":
            coupons.append(
                Coupon(
                    id=coupon_id,
                    kind="flat",
                    value=rng.choice([40, 75, 100, 150]),
                    query=f"subtotal >= {rng.randrange(100, 400)}",
                )
            )
        elif kind == "percent":
            scoped = product_ids and rng.random() < 0.5
            coupons.append(
                Coupon(
                    id=coupon_id,
                    kind="percent",
                    value=rng.choice([10, 25, 40, 60]),
                    cap=rng.choice([None, 50, 120]),
                    query=rng.choice(
                        [
                            None,
                            f"select_subtotal >= {rng.randrange(50, 250)}",
                            f"subtotal >= {rng.randrange(100, 400)}",
                        ]
                    ),
                    applies_to=(
                        frozenset(
                            rng.sample(product_ids, rng.randint(1, len(product_ids)))
                        )
                        if scoped
                        else None
                    ),
                )
            )
        else:
            coupons.append(
                Coupon(
                    id=coupon_id,
                    kind="free_delivery",
                    query=rng.choice(
                        [None, "user.member == true", f"subtotal >= {rng.randrange(80, 250)}"]
                    ),
                )
            )
    menu = Menu(
        restaurant="fuzz",
        items=tuple(items),
        coupons=tuple(coupons),
        combos=tuple(combos),
    )
    user = User(member=rng.random() < 0.5, first_order=rng.random() < 0.3)
    config = PricingConfig(
        delivery_fee=rng.choice([0, 29, 49]),
        platform_fee=rng.choice([0, 5]),
        gst_rate=rng.choice([0.0, 0.05]),
    )
    budget = rng.randrange(80, 601)
    return menu, user, config, budget
