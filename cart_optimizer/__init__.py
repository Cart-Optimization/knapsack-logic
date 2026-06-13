"""Best-value food cart optimizer: exact, coupon-aware, budget-constrained."""

from .models import (
    AddonGroup,
    AddonOption,
    Cart,
    CartLine,
    Combo,
    ComboLine,
    Coupon,
    Item,
    ItemLine,
    Menu,
    MenuError,
    PricingConfig,
    User,
    Variant,
)
from .optimizer import best_cart
from .pricing import PriceBreakdown, SolveResult, is_eligible, price_cart

__version__ = "0.2.0"

__all__ = [
    "AddonGroup",
    "AddonOption",
    "Cart",
    "CartLine",
    "Combo",
    "ComboLine",
    "Coupon",
    "Item",
    "ItemLine",
    "Menu",
    "MenuError",
    "PriceBreakdown",
    "PricingConfig",
    "SolveResult",
    "User",
    "Variant",
    "best_cart",
    "is_eligible",
    "price_cart",
    "__version__",
]
