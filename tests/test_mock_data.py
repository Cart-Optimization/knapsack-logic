"""The bundled demo menu parses and the optimizer solves it sensibly."""

from cart_optimizer.mock_data import demo_menu
from cart_optimizer.models import PricingConfig, User
from cart_optimizer.optimizer import best_cart
from tests.helpers import assert_valid_result


def test_demo_menu_parses():
    menu = demo_menu()
    assert len(menu.items) == 7 and len(menu.coupons) == 3 and len(menu.combos) == 2
    # availability + lunchtime window filtering
    orderable = [item.id for item in menu.orderable_items("13:00")]
    assert "itm_brownie" not in orderable and "itm_idli" not in orderable
    assert "itm_margherita" in orderable
    # v2 surface is present
    margherita = next(i for i in menu.items if i.id == "itm_margherita")
    assert margherita.addons and margherita.addons[0].id == "grp_toppings"
    assert next(i for i in menu.items if i.id == "itm_thums_up").max_quantity == 3


def test_demo_menu_solves_within_budget():
    menu = demo_menu()
    user = User(member=True)
    config = PricingConfig(delivery_fee=29, platform_fee=5, gst_rate=0.05)
    result = best_cart(menu, user, config, budget=300, now="13:00")
    assert result.cart.lines, "demo budget should afford something"
    assert result.breakdown.total <= 300
    assert_valid_result(result, menu, user, config, 300)


def test_welcome_combo_only_for_first_order_users():
    menu = demo_menu()
    config = PricingConfig(delivery_fee=29, platform_fee=5, gst_rate=0.05)
    newcomer = best_cart(menu, User(first_order=True), config, budget=300, now="13:00")
    assert any(
        getattr(line, "product_id", None) == "cmb_welcome" for line in newcomer.cart.lines
    )
    regular = best_cart(menu, User(), config, budget=300, now="13:00")
    assert all(
        getattr(line, "product_id", None) != "cmb_welcome" for line in regular.cart.lines
    )
