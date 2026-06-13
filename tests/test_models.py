"""Tests for the domain model and menu parsing."""

import datetime as dt

import pytest

from cart_optimizer.models import (
    Cart,
    CartLine,
    Coupon,
    Item,
    Menu,
    MenuError,
    PricingConfig,
    User,
    Variant,
)


def mk_item(suffix, cost, pref, **kwargs):
    return Item(
        id=f"itm_{suffix}",
        name=suffix,
        preference=pref,
        variants=(Variant(id=f"var_{suffix}", name="Standard", cost=cost),),
        **kwargs,
    )


# --- Variant -----------------------------------------------------------------

def test_variant_ok():
    v = Variant(id="var_x", name="Regular", cost=199)
    assert v.cost == 199


@pytest.mark.parametrize("bad_id", ["x", "itm_x", "var_", 7])
def test_variant_bad_id(bad_id):
    with pytest.raises(MenuError):
        Variant(id=bad_id, name="n", cost=10)


@pytest.mark.parametrize("bad_cost", [-1, 19.5, True, "199", None])
def test_variant_bad_cost(bad_cost):
    with pytest.raises(MenuError):
        Variant(id="var_x", name="n", cost=bad_cost)


# --- Item ---------------------------------------------------------------------

def test_item_ok():
    item = mk_item("pizza", 199, 0.9)
    assert item.available and item.variants[0].cost == 199


@pytest.mark.parametrize("bad_pref", [-0.1, 1.1, "high", None])
def test_item_bad_preference(bad_pref):
    with pytest.raises(MenuError):
        mk_item("pizza", 199, bad_pref)


def test_item_requires_variants():
    with pytest.raises(MenuError):
        Item(id="itm_x", name="x", preference=0.5, variants=())


def test_item_duplicate_variant_ids():
    v = Variant(id="var_x", name="n", cost=10)
    with pytest.raises(MenuError):
        Item(id="itm_x", name="x", preference=0.5, variants=(v, v))


def test_item_bad_time_window():
    with pytest.raises(MenuError):
        mk_item("x", 10, 0.5, time_window=("11am", "23:00"))


def test_is_orderable_availability():
    assert not mk_item("x", 10, 0.5, available=False).is_orderable()


def test_is_orderable_window():
    item = mk_item("x", 10, 0.5, time_window=("11:00", "23:00"))
    assert item.is_orderable(dt.time(12, 0))
    assert item.is_orderable("12:30")          # string accepted
    assert not item.is_orderable(dt.time(9, 0))
    assert item.is_orderable(None)             # no clock -> no filtering


def test_is_orderable_wraparound_window():
    item = mk_item("x", 10, 0.5, time_window=("22:00", "02:00"))
    assert item.is_orderable(dt.time(23, 0))
    assert item.is_orderable(dt.time(1, 0))
    assert not item.is_orderable(dt.time(12, 0))


# --- Coupon ---------------------------------------------------------------------

def test_flat_coupon_ok():
    c = Coupon(id="off_flat", kind="flat", value=100, query="subtotal >= 199")
    assert not c.is_scoped


def test_scoped_percent_coupon_ok():
    c = Coupon(
        id="off_p", kind="percent", value=30, cap=120,
        query="select_subtotal >= 150", applies_to=["itm_a", "itm_b"],
    )
    assert c.is_scoped and c.applies_to == frozenset({"itm_a", "itm_b"})


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(kind="mystery", value=10),                       # unknown kind
        dict(kind="flat", value=0),                           # flat needs value
        dict(kind="flat", value=100, cap=50),                 # cap only for percent
        dict(kind="percent", value=0),                        # percent bounds
        dict(kind="percent", value=101),
        dict(kind="percent", value=10, cap=-5),
        dict(kind="free_delivery", value=20),                 # carries no value
        dict(kind="free_delivery", applies_to=["itm_a"]),     # cannot be scoped
        dict(kind="flat", value=10, applies_to=[]),           # empty scope
        dict(kind="flat", value=10, applies_to=["var_a"]),    # wrong prefix
    ],
)
def test_coupon_validation_errors(kwargs):
    with pytest.raises(MenuError):
        Coupon(id="off_x", **kwargs)


@pytest.mark.parametrize(
    "query",
    [
        "item_count >= 2",            # outside supported vocabulary
        "user.age > 10",              # unknown user field
        "__import__('os')",           # unsafe construct
        "subtotal >=",                # syntax error
    ],
)
def test_coupon_rejects_bad_queries_at_construction(query):
    with pytest.raises(MenuError):
        Coupon(id="off_x", kind="flat", value=10, query=query)


def test_coupon_accepts_supported_queries():
    Coupon(
        id="off_x", kind="flat", value=10,
        query="subtotal >= 199 and user.member == true",
    )


# --- User / PricingConfig ----------------------------------------------------------

def test_user_context():
    assert User(member=True).as_context() == {"member": True, "first_order": False}


@pytest.mark.parametrize(
    "kwargs",
    [dict(delivery_fee=-1), dict(platform_fee=-1), dict(gst_rate=-0.1), dict(gst_rate=1.0)],
)
def test_pricing_config_validation(kwargs):
    with pytest.raises(MenuError):
        PricingConfig(**kwargs)


# --- Cart ----------------------------------------------------------------------------

def test_cartline_variant_must_belong_to_item():
    a, b = mk_item("a", 100, 0.5), mk_item("b", 50, 0.5)
    with pytest.raises(MenuError):
        CartLine(item=a, variant=b.variants[0])


def test_cart_subtotals():
    a, b = mk_item("a", 100, 0.5), mk_item("b", 57, 0.4)
    cart = Cart((CartLine(a, a.variants[0]), CartLine(b, b.variants[0])))
    assert cart.subtotal == 157
    assert cart.select_subtotal(frozenset({"itm_a"})) == 100
    assert cart.select_subtotal(None) == 157


def test_cart_rejects_duplicate_items():
    a = mk_item("a", 100, 0.5)
    line = CartLine(a, a.variants[0])
    with pytest.raises(MenuError):
        Cart((line, line))


# --- Menu -----------------------------------------------------------------------------

def test_menu_duplicate_ids_rejected():
    a = mk_item("a", 100, 0.5)
    with pytest.raises(MenuError):
        Menu(restaurant="r", items=(a, a))
    with pytest.raises(MenuError):
        Menu(
            restaurant="r", items=(a,),
            coupons=(
                Coupon(id="off_x", kind="flat", value=10),
                Coupon(id="off_x", kind="flat", value=20),
            ),
        )


def test_menu_orderable_items_filters():
    ok = mk_item("ok", 100, 0.5)
    gone = mk_item("gone", 100, 0.9, available=False)
    breakfast = mk_item("idli", 80, 0.9, time_window=("07:00", "11:00"))
    menu = Menu(restaurant="r", items=(ok, gone, breakfast))
    assert [i.id for i in menu.orderable_items(dt.time(12, 0))] == ["itm_ok"]
    assert [i.id for i in menu.orderable_items(None)] == ["itm_ok", "itm_idli"]


SAMPLE_PAYLOAD = {
    "restaurant": "Test Kitchen",
    "items": {
        "itm_pizza": {
            "name": "Margherita",
            "preference": 0.9,
            "time_window": ["11:00", "23:00"],
            "variants": {
                "var_reg": {"name": "Regular", "cost": 199},
                "var_lrg": {"name": "Large", "cost": 299},
            },
        },
        "itm_bread": {"name": "Garlic Bread", "preference": 0.6, "cost": 120},
        "itm_soda": {"name": "Thums Up", "preference": 0.4, "variants": {"var_soda": 57}},
    },
    "offers": {
        "off_flat100": {"kind": "flat", "value": 100, "query": "subtotal >= 199"},
        "off_pizza30": {
            "kind": "percent", "value": 30, "cap": 120,
            "query": "select_subtotal >= 199", "applies_to": ["itm_pizza"],
        },
        "off_freedel": {"kind": "free_delivery", "query": "user.member == true"},
    },
}


def test_from_dict_parses_sample():
    menu = Menu.from_dict(SAMPLE_PAYLOAD)
    assert menu.restaurant == "Test Kitchen"
    assert len(menu.items) == 3 and len(menu.coupons) == 3
    pizza = menu.items[0]
    assert [v.cost for v in pizza.variants] == [199, 299]
    bread = menu.items[1]                      # bare cost -> one synthetic variant
    assert len(bread.variants) == 1 and bread.variants[0].cost == 120
    soda = menu.items[2]                       # int-shorthand variant
    assert soda.variants[0].cost == 57
    scoped = menu.coupons[1]
    assert scoped.applies_to == frozenset({"itm_pizza"})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["items"]["itm_pizza"].pop("preference"),
        lambda p: p["items"]["itm_bread"].pop("cost"),
        lambda p: p["offers"]["off_flat100"].pop("kind"),
    ],
)
def test_from_dict_missing_fields(mutate):
    import copy

    payload = copy.deepcopy(SAMPLE_PAYLOAD)
    mutate(payload)
    with pytest.raises(MenuError):
        Menu.from_dict(payload)
