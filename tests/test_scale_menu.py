"""Tests for menu price calibration (scale_menu_costs)."""

from cart_optimizer.discovery import scale_menu_costs
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
