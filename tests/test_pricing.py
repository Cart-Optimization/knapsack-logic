"""Tests for the shared money math (eligibility, discount, breakdown)."""

import pytest

from cart_optimizer.models import (
    Cart,
    CartLine,
    Coupon,
    Item,
    PricingConfig,
    User,
    Variant,
)
from cart_optimizer.pricing import (
    empty_breakdown,
    is_eligible,
    price_amounts,
    price_cart,
)


def mk_item(suffix, cost, pref=0.5):
    return Item(
        id=f"itm_{suffix}",
        name=suffix,
        preference=pref,
        variants=(Variant(id=f"var_{suffix}", name="Standard", cost=cost),),
    )


def mk_cart(*items):
    return Cart(tuple(CartLine(item, item.variants[0]) for item in items))


USER = User()
MEMBER = User(member=True)
FREE = PricingConfig()
FEES = PricingConfig(delivery_fee=30, platform_fee=5, gst_rate=0.05)


def test_empty_cart_costs_nothing():
    breakdown = price_cart(Cart(), None, USER, FEES)
    assert breakdown == empty_breakdown()
    assert breakdown.total == 0


def test_no_coupon_with_fees():
    cart = mk_cart(mk_item("a", 100), mk_item("b", 57))
    breakdown = price_cart(cart, None, USER, FEES)
    assert breakdown.subtotal == 157
    assert breakdown.tax == 7.85
    assert breakdown.total == 157 + 30 + 5 + 7.85


def test_flat_coupon_discounts_then_taxes():
    cart = mk_cart(mk_item("a", 250))
    coupon = Coupon(id="off_f", kind="flat", value=100, query="subtotal >= 199")
    breakdown = price_cart(cart, coupon, USER, FEES)
    assert breakdown.discount == 100
    assert breakdown.tax == 7.5  # 5% of 150
    assert breakdown.total == 150 + 30 + 5 + 7.5
    assert breakdown.coupon_id == "off_f"


def test_flat_discount_capped_at_subtotal():
    cart = mk_cart(mk_item("a", 80))
    coupon = Coupon(id="off_f", kind="flat", value=100)
    breakdown = price_cart(cart, coupon, USER, FEES)
    assert breakdown.discount == 80
    assert breakdown.total == 0 + 30 + 5 + 0


def test_ineligible_coupon():
    cart = mk_cart(mk_item("a", 150))
    coupon = Coupon(id="off_f", kind="flat", value=100, query="subtotal >= 199")
    assert not is_eligible(cart, coupon, USER)
    with pytest.raises(ValueError):
        price_cart(cart, coupon, USER, FREE)


def test_coupon_never_applies_to_empty_cart():
    coupon = Coupon(id="off_f", kind="flat", value=100)
    assert not is_eligible(Cart(), coupon, USER)


def test_percent_uncapped():
    cart = mk_cart(mk_item("a", 200))
    coupon = Coupon(id="off_p", kind="percent", value=30)
    assert price_cart(cart, coupon, USER, FREE).discount == 60


def test_percent_cap():
    cart = mk_cart(mk_item("a", 400))
    coupon = Coupon(id="off_p", kind="percent", value=50, cap=60)
    breakdown = price_cart(cart, coupon, USER, FREE)
    assert breakdown.discount == 60
    assert breakdown.total == 340


def test_scoped_percent_discounts_scope_only():
    pizza, soda = mk_item("pizza", 200), mk_item("soda", 100)
    cart = mk_cart(pizza, soda)
    coupon = Coupon(
        id="off_p", kind="percent", value=30,
        query="select_subtotal >= 150", applies_to=["itm_pizza"],
    )
    breakdown = price_cart(cart, coupon, USER, FREE)
    assert breakdown.discount == 60  # 30% of 200, soda untouched
    assert breakdown.total == 240


def test_scoped_query_uses_scope_subtotal():
    soda = mk_item("soda", 100)
    coupon = Coupon(
        id="off_p", kind="percent", value=30,
        query="select_subtotal >= 150", applies_to=["itm_pizza"],
    )
    # 100 in cart but 0 in scope -> not eligible
    assert not is_eligible(mk_cart(soda), coupon, USER)


def test_free_delivery_waives_fee_for_members_only():
    cart = mk_cart(mk_item("a", 100))
    coupon = Coupon(
        id="off_d", kind="free_delivery",
        query="user.member == true and subtotal >= 99",
    )
    assert not is_eligible(cart, coupon, USER)
    breakdown = price_cart(cart, coupon, MEMBER, FEES)
    assert breakdown.delivery_fee == 0
    assert breakdown.discount == 0
    assert breakdown.total == 100 + 0 + 5 + 5.0


def test_price_amounts_matches_price_cart():
    # The DP works at amount level; it must agree with cart-level pricing.
    cart = mk_cart(mk_item("pizza", 200), mk_item("soda", 100))
    coupon = Coupon(id="off_p", kind="percent", value=30, applies_to=["itm_pizza"])
    via_cart = price_cart(cart, coupon, USER, FEES)
    via_amounts = price_amounts(300, 200, coupon, USER, FEES)
    assert via_cart == via_amounts


def test_rounding_to_paise():
    cart = mk_cart(mk_item("a", 33))
    breakdown = price_cart(cart, None, USER, PricingConfig(gst_rate=0.05))
    assert breakdown.tax == 1.65
    assert breakdown.total == 34.65
