"""Tests for reading Swiggy's authoritative cart bill.

Coupons in this MCP surface through the *cart* (`get_food_cart` -> data.offers),
not the (always-empty) coupon-list endpoint. Swiggy auto-applies the best
coupon and reports the true discount, free-delivery flag, and final `to_pay`.
This is the authority the optimizer's estimate must defer to before anything is
shown for confirmation. Fixture is a real redacted capture (SWIGGYIT live).
"""

import json
from pathlib import Path

import pytest

from cart_optimizer.adapters.swiggy import CartBill, SwiggyAdapterError, parse_cart_bill

FIXTURES = Path(__file__).parent / "fixtures"


def test_reads_real_swiggyit_cart():
    response = json.loads((FIXTURES / "mcdonalds_cart_swiggyit.json").read_text())
    bill = parse_cart_bill(response)
    assert isinstance(bill, CartBill)
    assert bill.coupon_code == "SWIGGYIT"
    assert bill.coupon_discount == 80
    assert bill.free_delivery is True
    assert bill.item_total == 325
    assert bill.taxes_and_charges == 70.56
    assert bill.to_pay == 316          # authoritative — read, never recomputed
    assert bill.item_count == 1
    assert bill.cod_available is True


def test_auto_suggested_coupon_with_zero_discount_is_not_applied():
    # Per the tool's own note: coupon_applied with coupon_discount=0 means
    # auto-suggested, NOT actually applied — do not claim a coupon.
    response = {"data": {
        "item_count": 1,
        "pricing": {"item_total": 120, "delivery_charge": 39,
                    "taxes_and_charges": 24.0, "to_pay": 183},
        "offers": {"coupon_applied": "TRYNEW", "coupon_discount": 0,
                   "free_delivery_applied": False},
    }, "availablePaymentMethods": ["Cash on Delivery"]}
    bill = parse_cart_bill(response)
    assert bill.coupon_code is None
    assert bill.coupon_discount == 0
    assert bill.to_pay == 183


def test_no_offers_block_means_no_coupon():
    response = {"data": {
        "item_count": 2,
        "pricing": {"item_total": 200, "delivery_charge": 0,
                    "taxes_and_charges": 30.0, "to_pay": 230},
    }, "availablePaymentMethods": ["Cash on Delivery"]}
    bill = parse_cart_bill(response)
    assert bill.coupon_code is None and bill.free_delivery is False
    assert bill.to_pay == 230


def test_non_cod_payment_flagged():
    response = {"data": {
        "item_count": 1,
        "pricing": {"item_total": 100, "delivery_charge": 0,
                    "taxes_and_charges": 5.0, "to_pay": 105},
        "offers": {},
    }, "availablePaymentMethods": ["UPI", "Card"]}
    bill = parse_cart_bill(response)
    assert bill.cod_available is False


def test_missing_pricing_raises():
    with pytest.raises(SwiggyAdapterError):
        parse_cart_bill({"data": {"item_count": 0}})


def test_empty_response_raises():
    with pytest.raises(SwiggyAdapterError):
        parse_cart_bill({})
