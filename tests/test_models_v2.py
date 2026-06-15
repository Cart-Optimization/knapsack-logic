"""Tests for the v2 model additions: add-on groups, combos, quantities."""

import pytest

from cart_optimizer.models import (
    AddonGroup,
    AddonOption,
    Cart,
    Combo,
    ComboLine,
    Item,
    ItemLine,
    CartLine,  # backward-compat alias for ItemLine
    Menu,
    MenuError,
    User,
    Variant,
)


def mk_option(suffix, cost, pref=0.2):
    return AddonOption(id=f"opt_{suffix}", name=suffix, cost=cost, preference=pref)


def mk_group(suffix, options, min_select=0, max_select=1):
    return AddonGroup(
        id=f"grp_{suffix}", name=suffix,
        min_select=min_select, max_select=max_select, options=tuple(options),
    )


def mk_item(suffix, cost, pref=0.5, **kwargs):
    return Item(
        id=f"itm_{suffix}",
        name=suffix,
        preference=pref,
        variants=(Variant(id=f"var_{suffix}", name="Standard", cost=cost),),
        **kwargs,
    )


# --- AddonOption / AddonGroup -------------------------------------------------

def test_addon_option_ok():
    opt = mk_option("olive", 30)
    assert opt.cost == 30


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(id="x_bad", name="n", cost=10, preference=0.1),   # prefix
        dict(id="opt_x", name="n", cost=-1, preference=0.1),   # cost
        dict(id="opt_x", name="n", cost=9.5, preference=0.1),  # non-int cost
        dict(id="opt_x", name="n", cost=10, preference=1.5),   # pref range
    ],
)
def test_addon_option_validation(kwargs):
    with pytest.raises(MenuError):
        AddonOption(**kwargs)


def test_addon_group_ok():
    group = mk_group("toppings", [mk_option("a", 10), mk_option("b", 20)], 0, 2)
    assert group.min_select == 0 and group.max_select == 2


@pytest.mark.parametrize(
    "min_select,max_select,n_options",
    [
        (2, 1, 3),   # min > max
        (-1, 1, 2),  # negative min
        (0, 0, 2),   # max must be >= 1
        (0, 3, 2),   # max beyond option count
        (0, 1, 0),   # no options
    ],
)
def test_addon_group_bounds(min_select, max_select, n_options):
    options = tuple(mk_option(f"o{i}", 10) for i in range(n_options))
    with pytest.raises(MenuError):
        mk_group("g", options, min_select, max_select)


def test_addon_group_duplicate_options():
    opt = mk_option("dup", 10)
    with pytest.raises(MenuError):
        mk_group("g", [opt, opt], 0, 2)


def test_item_rejects_duplicate_option_ids_across_groups():
    opt = mk_option("same", 10)
    with pytest.raises(MenuError):
        mk_item("x", 100, addons=(mk_group("g1", [opt]), mk_group("g2", [opt])))


def test_item_max_quantity_validation():
    assert mk_item("x", 100, max_quantity=3).max_quantity == 3
    for bad in (0, -1, 1.5, True):
        with pytest.raises(MenuError):
            mk_item("x", 100, max_quantity=bad)


# --- ItemLine: configurations -------------------------------------------------------

DIP_A = mk_option("dip_a", 20, 0.2)
DIP_B = mk_option("dip_b", 0, 0.0)
TOP_1 = mk_option("top_1", 30, 0.15)
TOP_2 = mk_option("top_2", 50, 0.25)

BREAD = mk_item(
    "bread", 100, 0.5,
    addons=(
        mk_group("dip", [DIP_A, DIP_B], min_select=1, max_select=1),
        mk_group("top", [TOP_1, TOP_2], min_select=0, max_select=2),
    ),
    max_quantity=2,
)


def test_itemline_costs_and_preference_scale_with_quantity():
    line = ItemLine(item=BREAD, variant=BREAD.variants[0], addons=(DIP_A, TOP_2), quantity=2)
    assert line.cost == 2 * (100 + 20 + 50)
    assert line.preference == pytest.approx(2 * (0.5 + 0.2 + 0.25))
    assert line.product_id == "itm_bread"


def test_itemline_enforces_mandatory_group():
    with pytest.raises(MenuError):
        ItemLine(item=BREAD, variant=BREAD.variants[0], addons=())  # dip min 1


def test_itemline_enforces_group_max():
    item = mk_item("x", 50, addons=(mk_group("top", [TOP_1, TOP_2], 0, 1),))
    with pytest.raises(MenuError):
        ItemLine(item=item, variant=item.variants[0], addons=(TOP_1, TOP_2))


def test_itemline_rejects_foreign_or_duplicate_addons():
    foreign = mk_option("foreign", 5)
    with pytest.raises(MenuError):
        ItemLine(item=BREAD, variant=BREAD.variants[0], addons=(DIP_A, foreign))
    with pytest.raises(MenuError):
        ItemLine(item=BREAD, variant=BREAD.variants[0], addons=(DIP_A, DIP_A))


def test_itemline_quantity_bounds():
    with pytest.raises(MenuError):
        ItemLine(item=BREAD, variant=BREAD.variants[0], addons=(DIP_B,), quantity=3)
    with pytest.raises(MenuError):
        ItemLine(item=BREAD, variant=BREAD.variants[0], addons=(DIP_B,), quantity=0)


def test_cartline_alias_still_works():
    simple = mk_item("simple", 80)
    line = CartLine(simple, simple.variants[0])
    assert line.cost == 80 and line.quantity == 1


# --- Combo ---------------------------------------------------------------------------

def test_combo_ok_and_applicability():
    combo = Combo(
        id="cmb_meal", name="Meal", cost=239, preference=0.95,
        composition={"itm_pizza": 1, "itm_soda": 2},
        applicability="user.member == true",
    )
    assert combo.composition_dict == {"itm_pizza": 1, "itm_soda": 2}
    assert combo.is_orderable(User(member=True))
    assert not combo.is_orderable(User())


def test_combo_no_applicability_is_open_to_all():
    combo = Combo(id="cmb_x", name="x", cost=100, preference=0.5)
    assert combo.is_orderable(User())


def test_combo_unavailable():
    combo = Combo(id="cmb_x", name="x", cost=100, preference=0.5, available=False)
    assert not combo.is_orderable(User(member=True))


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(id="itm_x", cost=100, preference=0.5),                      # prefix
        dict(id="cmb_x", cost=-5, preference=0.5),                       # cost
        dict(id="cmb_x", cost=100, preference=6.0),                      # pref > MAX
        dict(id="cmb_x", cost=100, preference=0.5, max_quantity=0),      # qty
        dict(id="cmb_x", cost=100, preference=0.5,
             composition={"var_x": 1}),                                  # bad ref prefix
        dict(id="cmb_x", cost=100, preference=0.5,
             composition={"itm_x": 0}),                                  # qty must be >= 1
        dict(id="cmb_x", cost=100, preference=0.5,
             applicability="subtotal >= 100"),     # cart-state applicability deferred
    ],
)
def test_combo_validation(kwargs):
    with pytest.raises(MenuError):
        Combo(name="x", **kwargs)


def test_comboline():
    combo = Combo(id="cmb_meal", name="Meal", cost=239, preference=0.95, max_quantity=2)
    line = ComboLine(combo=combo, quantity=2)
    assert line.cost == 478 and line.preference == pytest.approx(1.9)
    assert line.product_id == "cmb_meal"
    with pytest.raises(MenuError):
        ComboLine(combo=combo, quantity=3)


# --- Cart with mixed lines --------------------------------------------------------------

def test_cart_mixes_item_and_combo_lines():
    simple = mk_item("simple", 80, 0.4)
    combo = Combo(id="cmb_meal", name="Meal", cost=239, preference=0.95)
    cart = Cart((ItemLine(simple, simple.variants[0]), ComboLine(combo)))
    assert cart.subtotal == 319
    assert cart.select_subtotal({"cmb_meal"}) == 239
    assert cart.select_subtotal({"itm_simple"}) == 80


def test_cart_rejects_duplicate_products_across_line_types():
    combo = Combo(id="cmb_meal", name="Meal", cost=239, preference=0.95, max_quantity=2)
    with pytest.raises(MenuError):
        Cart((ComboLine(combo), ComboLine(combo)))


def test_addon_cost_counts_in_scoped_subtotal():
    line = ItemLine(item=BREAD, variant=BREAD.variants[0], addons=(DIP_A,))
    cart = Cart((line,))
    assert cart.select_subtotal({"itm_bread"}) == 120  # base 100 + dip 20


# --- Menu with combos ----------------------------------------------------------------------

def test_menu_holds_combos_and_parses_from_dict():
    payload = {
        "restaurant": "r",
        "items": {
            "itm_pizza": {
                "name": "Pizza",
                "preference": 0.9,
                "cost": 199,
                "max_quantity": 2,
                "addons": {
                    "grp_cheese": {
                        "name": "Cheese",
                        "min": 0,
                        "max": 1,
                        "options": {
                            "opt_burst": {"name": "Burst", "cost": 60, "preference": 0.3}
                        },
                    }
                },
            }
        },
        "combos": {
            "cmb_meal": {
                "name": "Pizza Meal",
                "cost": 239,
                "preference": 0.95,
                "composition": {"itm_pizza": 1},
                "applicability": "user.first_order == true",
            }
        },
        "offers": {
            "off_combo50": {
                "kind": "percent", "value": 50, "applies_to": ["cmb_meal"],
            }
        },
    }
    menu = Menu.from_dict(payload)
    assert len(menu.combos) == 1
    pizza = menu.items[0]
    assert pizza.max_quantity == 2
    assert pizza.addons[0].options[0].cost == 60
    assert menu.coupons[0].applies_to == frozenset({"cmb_meal"})  # combos scopable
    assert [c.id for c in menu.orderable_combos(User(first_order=True))] == ["cmb_meal"]
    assert menu.orderable_combos(User()) == ()


def test_menu_rejects_duplicate_ids_between_items_and_combos():
    item = mk_item("x", 100)
    with pytest.raises(MenuError):
        Menu(
            restaurant="r",
            items=(item,),
            combos=(
                Combo(id="cmb_y", name="y", cost=50, preference=0.5),
                Combo(id="cmb_y", name="y2", cost=60, preference=0.5),
            ),
        )
