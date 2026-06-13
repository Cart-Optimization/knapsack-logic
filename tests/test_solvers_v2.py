"""Hand-built v2 cases (addons, quantities, combos) run through BOTH solvers.

Each case asserts the optimizer's exact behaviour and that the brute-force
oracle agrees, so these double as pinned equivalence points.
"""

import math

import pytest

from cart_optimizer.brute_force import best_cart_brute_force
from cart_optimizer.models import (
    AddonGroup,
    AddonOption,
    Combo,
    Coupon,
    Item,
    Menu,
    PricingConfig,
    User,
    Variant,
)
from cart_optimizer.optimizer import best_cart


def mk_option(suffix, cost, pref):
    return AddonOption(id=f"opt_{suffix}", name=suffix, cost=cost, preference=pref)


def mk_item(suffix, cost, pref, **kwargs):
    return Item(
        id=f"itm_{suffix}",
        name=suffix,
        preference=pref,
        variants=(Variant(id=f"var_{suffix}", name="Standard", cost=cost),),
        **kwargs,
    )


USER = User()
FREE = PricingConfig()


def solve_both(menu, user, config, budget, now=None):
    dp = best_cart(menu, user, config, budget, now=now)
    bf = best_cart_brute_force(menu, user, config, budget, now=now)
    assert math.isclose(dp.preference, bf.preference, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(
        dp.breakdown.total, bf.breakdown.total, rel_tol=0, abs_tol=1e-9
    )
    return dp


def test_mandatory_addon_group_is_always_satisfied():
    dip_a = mk_option("dip_a", 20, 0.2)
    dip_b = mk_option("dip_b", 0, 0.0)
    bread = mk_item(
        "bread", 100, 0.5,
        addons=(
            AddonGroup(
                id="grp_dip", name="dip", min_select=1, max_select=1,
                options=(dip_a, dip_b),
            ),
        ),
    )
    menu = Menu(restaurant="r", items=(bread,))
    # Tight budget: free dip is the only valid configuration that fits.
    result = solve_both(menu, USER, FREE, budget=105)
    assert [option.id for option in result.cart.lines[0].addons] == ["opt_dip_b"]
    # Looser budget: the paid dip's preference is worth it.
    result = solve_both(menu, USER, FREE, budget=200)
    assert [option.id for option in result.cart.lines[0].addons] == ["opt_dip_a"]
    assert result.breakdown.total == 120


def test_optional_addon_taken_only_when_budget_allows():
    cheese = mk_option("cheese", 60, 0.3)
    pizza = mk_item(
        "pizza", 199, 0.9,
        addons=(
            AddonGroup(
                id="grp_cheese", name="cheese", min_select=0, max_select=1,
                options=(cheese,),
            ),
        ),
    )
    menu = Menu(restaurant="r", items=(pizza,))
    assert solve_both(menu, USER, FREE, budget=220).cart.lines[0].addons == ()
    result = solve_both(menu, USER, FREE, budget=300)
    assert [option.id for option in result.cart.lines[0].addons] == ["opt_cheese"]


def test_quantity_fills_budget_when_allowed():
    soda = mk_item("soda", 57, 0.4, max_quantity=3)
    menu = Menu(restaurant="r", items=(soda,))
    result = solve_both(menu, USER, FREE, budget=200)
    assert result.cart.lines[0].quantity == 3
    assert result.breakdown.total == 171
    assert result.preference == pytest.approx(1.2)


def test_default_quantity_cap_is_one():
    soda = mk_item("soda", 57, 0.4)
    menu = Menu(restaurant="r", items=(soda,))
    result = solve_both(menu, USER, FREE, budget=200)
    assert result.cart.lines[0].quantity == 1


def test_combo_wins_when_items_break_budget():
    pizza = mk_item("pizza", 199, 0.9)
    soda = mk_item("soda", 57, 0.4)
    meal = Combo(
        id="cmb_meal", name="Pizza Meal", cost=239, preference=0.95,
        composition={"itm_pizza": 1, "itm_soda": 1},
    )
    menu = Menu(restaurant="r", items=(pizza, soda), combos=(meal,))
    # Roomy budget: combo composes WITH the soda item (296, pref 1.35) and
    # beats both pizza+soda (256, 1.3) and the combo alone (239, 0.95).
    roomy = solve_both(menu, USER, FREE, budget=300)
    assert sorted(line.product_id for line in roomy.cart.lines) == [
        "cmb_meal", "itm_soda",
    ]
    assert roomy.preference == pytest.approx(1.35)
    # 270: combo+soda (296) breaks the budget; the item pair is best.
    mid = solve_both(menu, USER, FREE, budget=270)
    assert sorted(line.product_id for line in mid.cart.lines) == [
        "itm_pizza", "itm_soda",
    ]
    # 240: the item pair (256) breaks the budget too; the bundle fits.
    tight = solve_both(menu, USER, FREE, budget=240)
    assert [line.product_id for line in tight.cart.lines] == ["cmb_meal"]
    assert tight.breakdown.total == 239


def test_combo_applicability_filters_by_user():
    meal = Combo(
        id="cmb_first", name="First-order deal", cost=149, preference=0.9,
        applicability="user.first_order == true",
    )
    pizza = mk_item("pizza", 199, 0.7)
    menu = Menu(restaurant="r", items=(pizza,), combos=(meal,))
    newcomer = solve_both(menu, User(first_order=True), FREE, budget=200)
    assert [line.product_id for line in newcomer.cart.lines] == ["cmb_first"]
    regular = solve_both(menu, USER, FREE, budget=200)
    assert [line.product_id for line in regular.cart.lines] == ["itm_pizza"]


def test_scoped_coupon_on_combo():
    meal = Combo(id="cmb_meal", name="Meal", cost=300, preference=0.6)
    side = mk_item("side", 90, 0.55)
    menu = Menu(
        restaurant="r",
        items=(side,),
        combos=(meal,),
        coupons=(
            Coupon(
                id="off_c", kind="percent", value=50,
                query="select_subtotal >= 200", applies_to=["cmb_meal"],
            ),
        ),
    )
    result = solve_both(menu, USER, FREE, budget=250)
    assert sorted(line.product_id for line in result.cart.lines) == [
        "cmb_meal", "itm_side",
    ]
    assert result.breakdown.discount == 150  # 50% of the combo only
    assert result.breakdown.total == 240
    assert result.coupon.id == "off_c"


def test_addon_cost_counts_toward_scoped_coupon():
    cheese = mk_option("cheese", 60, 0.3)
    pizza = mk_item(
        "pizza", 150, 0.9,
        addons=(
            AddonGroup(
                id="grp_cheese", name="cheese", min_select=0, max_select=1,
                options=(cheese,),
            ),
        ),
    )
    menu = Menu(
        restaurant="r",
        items=(pizza,),
        coupons=(
            Coupon(
                id="off_p", kind="percent", value=50,
                query="select_subtotal >= 200", applies_to=["itm_pizza"],
            ),
        ),
    )
    # Plain pizza (150) misses the >=200 scope threshold; with cheese the
    # line is 210, unlocking 50% off -> 105 final. The addon PAYS.
    result = solve_both(menu, USER, FREE, budget=130)
    assert [option.id for option in result.cart.lines[0].addons] == ["opt_cheese"]
    assert result.breakdown.total == 105
    assert result.coupon.id == "off_p"


def test_quantity_crosses_flat_coupon_threshold():
    soda = mk_item("soda", 57, 0.4, max_quantity=4)
    menu = Menu(
        restaurant="r",
        items=(soda,),
        coupons=(Coupon(id="off_f", kind="flat", value=100, query="subtotal >= 199"),),
    )
    # 4 sodas = 228 >= 199 -> -100 = 128: more sodas AND cheaper than 2 (114)?
    # 2 sodas cost 114 with pref 0.8; 4 sodas cost 128 with pref 1.6.
    result = solve_both(menu, USER, FREE, budget=130)
    assert result.cart.lines[0].quantity == 4
    assert result.breakdown.total == 128
    assert result.preference == pytest.approx(1.6)
