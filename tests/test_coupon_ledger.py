"""Tests for the per-branch coupon ledger and its verifier integration."""

import json
from pathlib import Path

from cart_optimizer.adapters.swiggy import CartBill
from cart_optimizer.adapters.swiggy_session import SwiggyOps, SwiggySessionVerifier
from cart_optimizer.coupon_ledger import (
    InMemoryCouponLedger,
    JsonCouponLedger,
    SqliteCouponLedger,
    PRUNE_AFTER_MISSES,
)
import pytest
from cart_optimizer.models import Cart, ItemLine, Item, Variant

RID = "668678"


# ── ledger unit tests ─────────────────────────────────────────────────────────

def test_records_and_returns_working_code():
    led = InMemoryCouponLedger()
    assert led.known(RID) == []
    led.record(RID, "SWIGGYIT", 80)
    assert led.known(RID) == ["SWIGGYIT"]


def test_known_is_scoped_per_branch():
    led = InMemoryCouponLedger()
    led.record("111", "FLAT100", 99)
    led.record("222", "FLAT75", 75)
    assert led.known("111") == ["FLAT100"]
    assert led.known("222") == ["FLAT75"]
    assert led.known("333") == []


def test_known_ranks_by_best_discount():
    led = InMemoryCouponLedger()
    led.record(RID, "SMALL", 30)
    led.record(RID, "BIG", 120)
    led.record(RID, "MID", 75)
    assert led.known(RID) == ["BIG", "MID", "SMALL"]


def test_failed_codes_pruned_after_repeated_misses():
    led = InMemoryCouponLedger()
    for _ in range(PRUNE_AFTER_MISSES):
        led.record(RID, "DEAD", 0)
    assert "DEAD" not in led.known(RID)


def test_a_hit_keeps_code_alive_despite_misses():
    led = InMemoryCouponLedger()
    led.record(RID, "REAL", 50)
    for _ in range(PRUNE_AFTER_MISSES + 2):
        led.record(RID, "REAL", 0)   # later misses (expired that day, etc.)
    assert "REAL" in led.known(RID)   # proven codes stay


def test_json_ledger_persists_across_instances(tmp_path):
    p = tmp_path / "coupons.json"
    led = JsonCouponLedger(p)
    led.record(RID, "SWIGGYIT", 80)
    # New instance reads from disk.
    led2 = JsonCouponLedger(p)
    assert led2.known(RID) == ["SWIGGYIT"]
    assert json.loads(p.read_text())[RID]["SWIGGYIT"]["best_discount"] == 80.0


def test_json_ledger_survives_corrupt_file(tmp_path):
    p = tmp_path / "coupons.json"
    p.write_text("{ not valid json")
    led = JsonCouponLedger(p)   # must not raise
    assert led.known(RID) == []
    led.record(RID, "X", 10)
    assert led.known(RID) == ["X"]


# ── shared SQLite ledger ──────────────────────────────────────────────────────

@pytest.mark.parametrize("make", [
    lambda tmp: InMemoryCouponLedger(),
    lambda tmp: JsonCouponLedger(tmp / "c.json"),
    lambda tmp: SqliteCouponLedger(tmp / "c.db"),
])
def test_all_ledgers_share_the_same_contract(make, tmp_path):
    """Every implementation must behave identically for the verifier."""
    led = make(tmp_path)
    assert led.known(RID) == []
    led.record(RID, "BIG", 120)
    led.record(RID, "SMALL", 30)
    led.record(RID, "OTHER", 50)   # different... same branch
    assert led.known(RID)[0] == "BIG"          # ranked by best discount
    assert set(led.known(RID)) == {"BIG", "SMALL", "OTHER"}
    for _ in range(PRUNE_AFTER_MISSES):
        led.record(RID, "DEAD", 0)
    assert "DEAD" not in led.known(RID)         # pruned


def test_sqlite_is_shared_across_connections(tmp_path):
    p = tmp_path / "shared.db"
    user_a = SqliteCouponLedger(p)
    user_b = SqliteCouponLedger(p)            # a different "user"/process
    user_a.record(RID, "SWIGGYIT", 80)        # discovered by user A
    assert user_b.known(RID) == ["SWIGGYIT"]  # immediately visible to user B


def test_sqlite_persists_across_restart(tmp_path):
    p = tmp_path / "persist.db"
    SqliteCouponLedger(p).record(RID, "FLAT100", 99)
    assert SqliteCouponLedger(p).known(RID) == ["FLAT100"]


def test_sqlite_all_branches_view(tmp_path):
    led = SqliteCouponLedger(tmp_path / "c.db")
    led.record("111", "A", 10)
    led.record("222", "B", 20)
    branches = led.all_branches()
    assert branches == {"111": ["A"], "222": ["B"]}


# ── verifier integration ──────────────────────────────────────────────────────

def _item():
    return Item(id="itm_1", name="x", preference=0.9, variants=(Variant("var_1", "s", 100),))


def _bill_resp(to_pay, coupon=None, discount=0):
    return {
        "data": {
            "item_count": 1,
            "pricing": {"item_total": 100.0, "delivery_charge": 0,
                        "taxes_and_charges": 10.0, "to_pay": to_pay},
            "offers": {"coupon_applied": coupon, "coupon_discount": discount,
                       "free_delivery_applied": bool(coupon)},
        },
        "availablePaymentMethods": ["Cash on Delivery"],
    }


class MockOps:
    def __init__(self, cart_responses):
        self.cart_responses = cart_responses
        self.applied = []

    def flush(self): pass
    def update(self, *a): pass
    def apply_coupon(self, code, addr): self.applied.append(code)
    def get_cart(self, addr): return self.cart_responses.pop(0)

    def as_ops(self):
        return SwiggyOps(self.flush, self.update, self.apply_coupon, self.get_cart)


def test_verifier_records_working_coupon_to_ledger():
    led = InMemoryCouponLedger()
    mock = MockOps([_bill_resp(200), _bill_resp(120, coupon="SWIGGYIT", discount=80)])
    v = SwiggySessionVerifier(mock.as_ops(), RID, "addr",
                              coupon_codes=["SWIGGYIT"], ledger=led)
    v.verify(Cart((ItemLine(_item(), _item().variants[0]),)))
    assert led.known(RID) == ["SWIGGYIT"]


def test_verifier_tries_branch_known_codes_first():
    led = InMemoryCouponLedger()
    led.record(RID, "BRANCHCODE", 90)   # remembered from a prior order
    # base, then BRANCHCODE applied successfully
    mock = MockOps([_bill_resp(200), _bill_resp(110, coupon="BRANCHCODE", discount=90)])
    v = SwiggySessionVerifier(mock.as_ops(), RID, "addr",
                              coupon_codes=["SOMETHINGELSE"], ledger=led)
    bill = v.verify(Cart((ItemLine(_item(), _item().variants[0]),)))
    assert bill.coupon_code == "BRANCHCODE"
    assert mock.applied[0] == "BRANCHCODE"   # tried before the generic candidate
