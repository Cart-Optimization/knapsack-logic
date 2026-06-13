"""Mock restaurant menu in the normalized schema.

Mirrors the spec's flavour: prefixed ids, preferences, availability flags,
time windows, choose-one variants, add-on groups (grp_/opt_), per-item
quantity caps, combos (cmb_) with user-status applicability, and the three
coupon shapes. Used by the demo and as a stand-in until a real Swiggy MCP
menu is captured and adapted.
"""

from .models import Menu

DEMO_MENU_DICT = {
    "restaurant": "Pizza Palace (mock)",
    "items": {
        "itm_margherita": {
            "name": "Margherita Pizza",
            "preference": 0.9,
            "time_window": ["11:00", "23:00"],
            "variants": {
                "var_marg_reg": {"name": "Regular", "cost": 199},
                "var_marg_lrg": {"name": "Large", "cost": 299},
            },
            "addons": {
                "grp_toppings": {
                    "name": "Extra toppings",
                    "min": 0,
                    "max": 2,
                    "options": {
                        "opt_cheese_burst": {"name": "Cheese Burst", "cost": 60, "preference": 0.12},
                        "opt_jalapeno": {"name": "Jalapeños", "cost": 30, "preference": 0.08},
                    },
                }
            },
        },
        "itm_farmhouse": {
            "name": "Farmhouse Pizza",
            "preference": 0.8,
            "variants": {
                "var_farm_reg": {"name": "Regular", "cost": 249},
                "var_farm_lrg": {"name": "Large", "cost": 349},
            },
        },
        "itm_garlic_bread": {"name": "Garlic Bread", "preference": 0.6, "cost": 120},
        "itm_choco_lava": {"name": "Choco Lava Cake", "preference": 0.7, "cost": 99},
        "itm_thums_up": {
            "name": "Thums Up",
            "preference": 0.4,
            "cost": 57,
            "max_quantity": 3,
        },
        "itm_idli": {
            "name": "Idli (breakfast only)",
            "preference": 0.95,
            "cost": 80,
            "time_window": ["07:00", "11:00"],
        },
        "itm_brownie": {
            "name": "Brownie (out of stock)",
            "preference": 0.85,
            "cost": 110,
            "available": False,
        },
    },
    "combos": {
        "cmb_pizza_meal": {
            "name": "Pizza + Drink Combo",
            "cost": 239,
            "preference": 0.95,
            "composition": {"itm_margherita": 1, "itm_thums_up": 1},
            "description": "Margherita + Thums Up at a bundle price",
        },
        "cmb_welcome": {
            "name": "First-Order Welcome Meal",
            "cost": 149,
            "preference": 0.9,
            "composition": {"itm_farmhouse": 1},
            "applicability": "user.first_order == true",
            "description": "Discounted Farmhouse for first-time users",
        },
    },
    "offers": {
        "off_flat100": {
            "kind": "flat",
            "value": 100,
            "query": "subtotal >= 199",
            "description": "₹100 off on orders above ₹199",
        },
        "off_pizza30": {
            "kind": "percent",
            "value": 30,
            "cap": 120,
            "query": "select_subtotal >= 199",
            "applies_to": ["itm_margherita", "itm_farmhouse"],
            "description": "30% off pizzas (max ₹120)",
        },
        "off_freedel": {
            "kind": "free_delivery",
            "query": "user.member == true and subtotal >= 99",
            "description": "Free delivery for members above ₹99",
        },
    },
}


def demo_menu() -> Menu:
    return Menu.from_dict(DEMO_MENU_DICT)
