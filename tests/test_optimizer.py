"""Hand-built cases for the exact DP optimizer.

Each case pins a behaviour the plain knapsack could not produce — most
importantly the coupon step function, where ADDING an item LOWERS the final
price by crossing a threshold.
"""

import datetime as dt

import pytest

from cart_optimizer.models import Coupon, Item, Menu, PricingConfig, User, Variant
from cart_optimizer.optimizer import best_cart
from tests.helpers import assert_valid_result


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


def ids(result):
    return sorted(line.item.id for line in result.cart.lines)


def test_coupon_step_function_adding_item_lowers_price():
    # Budget 120. {A} alone costs 150 -> infeasible. Adding B pushes the
    # subtotal to 210, crossing the >=199 threshold: -100 makes it 110,
    # which fits. Plain knapsack can never see this.
    menu = Menu(
        restaurant="r",
        items=(mk_item("a", 150, 0.9), mk_item("b", 60, 0.2)),
        coupons=(Coupon(id="off_f", kind="flat", value=100, query="subtotal >= 199"),),
    )
    result = best_cart(menu, USER, FREE, budget=120)
    assert ids(result) == ["itm_a", "itm_b"]
    assert result.coupon.id == "off_f"
    assert result.breakdown.total == 110
    assert result.preference == pytest.approx(1.1)
    assert_valid_result(result, menu, USER, FREE, 120)


def test_variant_upgrade_when_coupon_makes_large_cheaper():
    # Same preference either way, but Large+coupon (299-120=179) beats
    # Regular without coupon (199) on the price tie-break.
    item = Item(
        id="itm_pizza",
        name="pizza",
        preference=0.9,
        variants=(
            Variant(id="var_reg", name="Regular", cost=199),
            Variant(id="var_lrg", name="Large", cost=299),
        ),
    )
    menu = Menu(
        restaurant="r",
        items=(item,),
        coupons=(Coupon(id="off_f", kind="flat", value=120, query="subtotal >= 299"),),
    )
    result = best_cart(menu, USER, FREE, budget=400)
    assert len(result.cart.lines) == 1  # choose-one enforced
    assert result.cart.lines[0].variant.id == "var_lrg"
    assert result.coupon.id == "off_f"
    assert result.breakdown.total == 179


def test_free_delivery_membership():
    menu = Menu(
        restaurant="r",
        items=(mk_item("a", 100, 0.5),),
        coupons=(
            Coupon(
                id="off_d",
                kind="free_delivery",
                query="user.member == true and subtotal >= 99",
            ),
        ),
    )
    config = PricingConfig(delivery_fee=40)
    member = best_cart(menu, User(member=True), config, budget=200)
    assert member.coupon.id == "off_d"
    assert member.breakdown.delivery_fee == 0 and member.breakdown.total == 100
    guest = best_cart(menu, USER, config, budget=200)
    assert guest.coupon is None and guest.breakdown.total == 140


def test_scoped_percent_two_knapsack():
    menu = Menu(
        restaurant="r",
        items=(mk_item("pizza", 200, 0.6), mk_item("soda", 60, 0.55)),
        coupons=(
            Coupon(
                id="off_p",
                kind="percent",
                value=50,
                query="select_subtotal >= 150",
                applies_to=["itm_pizza"],
            ),
        ),
    )
    result = best_cart(menu, USER, FREE, budget=170)
    assert ids(result) == ["itm_pizza", "itm_soda"]
    assert result.breakdown.discount == 100  # 50% of pizza only
    assert result.breakdown.total == 160
    assert_valid_result(result, menu, USER, FREE, 170)


def test_percent_cap_respected():
    menu = Menu(
        restaurant="r",
        items=(mk_item("a", 400, 0.8),),
        coupons=(Coupon(id="off_p", kind="percent", value=50, cap=60),),
    )
    result = best_cart(menu, USER, FREE, budget=400)
    assert result.breakdown.discount == 60 and result.breakdown.total == 340


def test_budget_infeasible_returns_empty_cart():
    menu = Menu(restaurant="r", items=(mk_item("a", 500, 0.9),))
    result = best_cart(menu, USER, FREE, budget=100)
    assert result.cart.lines == ()
    assert result.coupon is None and result.breakdown.total == 0


def test_unavailable_and_off_hours_items_excluded():
    menu = Menu(
        restaurant="r",
        items=(
            mk_item("gone", 50, 1.0, available=False),
            mk_item("idli", 50, 0.9, time_window=("07:00", "11:00")),
            mk_item("ok", 50, 0.3),
        ),
    )
    result = best_cart(menu, USER, FREE, budget=300, now=dt.time(12, 0))
    assert ids(result) == ["itm_ok"]


def test_tie_prefers_cheaper_cart():
    menu = Menu(
        restaurant="r",
        items=(mk_item("a", 100, 0.5), mk_item("b", 80, 0.5)),
    )
    result = best_cart(menu, USER, FREE, budget=100)
    assert ids(result) == ["itm_b"] and result.breakdown.total == 80


def test_delivery_fee_counts_against_budget():
    menu = Menu(restaurant="r", items=(mk_item("a", 100, 0.5),))
    config = PricingConfig(delivery_fee=30)
    assert best_cart(menu, USER, config, budget=110).cart.lines == ()
    assert ids(best_cart(menu, USER, config, budget=130)) == ["itm_a"]


def test_gst_counts_against_budget():
    menu = Menu(restaurant="r", items=(mk_item("a", 100, 0.5),))
    config = PricingConfig(gst_rate=0.05)
    assert best_cart(menu, USER, config, budget=104).cart.lines == ()
    assert ids(best_cart(menu, USER, config, budget=105)) == ["itm_a"]


def test_negative_budget_rejected():
    menu = Menu(restaurant="r", items=(mk_item("a", 50, 0.5),))
    with pytest.raises(ValueError):
        best_cart(menu, USER, FREE, budget=-1)
