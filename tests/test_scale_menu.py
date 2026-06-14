"""Tests for menu price calibration (scale_menu_costs)."""

from cart_optimizer.discovery import scale_menu_costs, apply_real_prices
from cart_optimizer.models import (
    AddonGroup, AddonOption, Combo, Item, Menu, Variant,
)


def _menu():
    item = Item(
        id="itm_1", name="Taco", preference=0.9,
        variants=(Variant("var_1", "std", 100),),
        addons=(AddonGroup("grp_1", "extras", 0, 1,
                           (AddonOption("opt_1", "cheese", 50, 0.0),)),),
    )
    combo = Combo(id="cmb_1", name="meal", cost=200, preference=1.0)
    return Menu(restaurant="r", items=(item,), combos=(combo,))


def test_scales_all_prices():
    scaled = scale_menu_costs(_menu(), 0.5)
    it = scaled.items[0]
    assert it.variants[0].cost == 50
    assert it.addons[0].options[0].cost == 25
    assert scaled.combos[0].cost == 100


def test_scale_keeps_costs_non_negative_ints():
    scaled = scale_menu_costs(_menu(), 0.001)
    v = scaled.items[0].variants[0]
    assert isinstance(v.cost, int) and v.cost >= 0


def test_scale_up_factor():
    scaled = scale_menu_costs(_menu(), 1.5)
    assert scaled.items[0].variants[0].cost == 150


def test_apply_real_prices_sets_item_cost():
    m = apply_real_prices(_menu(), {"itm_1": 60})   # real price ₹60 (list was 100)
    assert m.items[0].variants[0].cost == 60


def test_apply_real_prices_leaves_unknown_items():
    m = apply_real_prices(_menu(), {"itm_other": 10})
    assert m.items[0].variants[0].cost == 100   # untouched


def test_apply_real_prices_scales_multivariant_proportionally():
    from cart_optimizer.models import Item, Menu, Variant
    item = Item(id="itm_2", name="Latte", preference=0.9,
                variants=(Variant("var_s", "S", 100), Variant("var_l", "L", 150)))
    m = apply_real_prices(Menu(restaurant="r", items=(item,)), {"itm_2": 60})
    costs = sorted(v.cost for v in m.items[0].variants)
    assert costs == [60, 90]   # base 100→60 (×0.6), 150→90
