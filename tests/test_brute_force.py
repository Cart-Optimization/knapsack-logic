"""Hand-verified cases for the brute-force oracle (the oracle itself must be
trustworthy before it can vouch for the DP)."""

import pytest

from cart_optimizer.brute_force import best_cart_brute_force
from cart_optimizer.models import Coupon, Item, Menu, PricingConfig, User, Variant


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


def test_picks_best_subset_under_budget():
    menu = Menu(
        restaurant="r",
        items=(mk_item("a", 100, 0.6), mk_item("b", 150, 0.5)),
    )
    result = best_cart_brute_force(menu, USER, FREE, budget=200)
    assert ids(result) == ["itm_a"]
    result = best_cart_brute_force(menu, USER, FREE, budget=260)
    assert ids(result) == ["itm_a", "itm_b"]
    assert result.preference == pytest.approx(1.1)


def test_flat_coupon_unlocks_bigger_cart():
    menu = Menu(
        restaurant="r",
        items=(mk_item("a", 100, 0.6), mk_item("b", 150, 0.5)),
        coupons=(Coupon(id="off_f", kind="flat", value=50, query="subtotal >= 200"),),
    )
    result = best_cart_brute_force(menu, USER, FREE, budget=210)
    assert ids(result) == ["itm_a", "itm_b"]
    assert result.coupon.id == "off_f"
    assert result.breakdown.total == 200


def test_tie_breaks_to_cheaper_cart():
    menu = Menu(
        restaurant="r",
        items=(mk_item("a", 100, 0.5), mk_item("b", 80, 0.5)),
    )
    result = best_cart_brute_force(menu, USER, FREE, budget=100)
    assert ids(result) == ["itm_b"]
    assert result.breakdown.total == 80


def test_free_delivery_for_member():
    menu = Menu(
        restaurant="r",
        items=(mk_item("a", 100, 0.5),),
        coupons=(
            Coupon(id="off_d", kind="free_delivery", query="user.member == true"),
        ),
    )
    config = PricingConfig(delivery_fee=40)
    member = best_cart_brute_force(menu, User(member=True), config, budget=200)
    assert member.coupon.id == "off_d" and member.breakdown.total == 100
    guest = best_cart_brute_force(menu, USER, config, budget=200)
    assert guest.coupon is None and guest.breakdown.total == 140


def test_scoped_percent():
    menu = Menu(
        restaurant="r",
        items=(mk_item("pizza", 200, 0.6), mk_item("soda", 60, 0.55)),
        coupons=(
            Coupon(
                id="off_p", kind="percent", value=50,
                query="select_subtotal >= 150", applies_to=["itm_pizza"],
            ),
        ),
    )
    result = best_cart_brute_force(menu, USER, FREE, budget=170)
    assert ids(result) == ["itm_pizza", "itm_soda"]
    assert result.breakdown.discount == 100
    assert result.breakdown.total == 160


def test_nothing_fits_returns_empty_cart():
    menu = Menu(restaurant="r", items=(mk_item("a", 500, 0.9),))
    result = best_cart_brute_force(menu, USER, FREE, budget=100)
    assert result.cart.lines == () and result.breakdown.total == 0


def test_negative_budget_rejected():
    menu = Menu(restaurant="r", items=(mk_item("a", 50, 0.5),))
    with pytest.raises(ValueError):
        best_cart_brute_force(menu, USER, FREE, budget=-1)
