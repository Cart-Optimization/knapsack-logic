"""Run the optimizer on the bundled mock menu.

    venv/bin/python -m cart_optimizer.demo --budget 300 --member --time 13:00

Fees default to a rough Swiggy-like model (₹29 delivery, ₹5 platform fee,
5% GST); the optimizer only needs them to *rank* carts — the authoritative
bill comes from Swiggy when the winning cart is built for review.
"""

from __future__ import annotations

import argparse
import dataclasses

from .mock_data import demo_menu
from .models import ComboLine, ItemLine, PricingConfig, User
from .optimizer import best_cart


def _line_label(line: ItemLine | ComboLine) -> str:
    """Human label for a cart line: item (+ variant + addons) or combo."""
    if isinstance(line, ComboLine):
        return f"{line.combo.name} (combo)"
    label = line.item.name
    if len(line.item.variants) > 1:
        label += f" — {line.variant.name}"
    if line.addons:
        label += " + " + ", ".join(option.name for option in line.addons)
    return label


def main() -> None:
    parser = argparse.ArgumentParser(description="Best-value cart demo (mock menu)")
    parser.add_argument("--budget", type=int, default=300, help="max final price, ₹")
    parser.add_argument("--member", action="store_true", help="user has membership")
    parser.add_argument("--first-order", action="store_true")
    parser.add_argument("--time", default=None, help="order time HH:MM (filters items)")
    parser.add_argument("--delivery", type=float, default=29.0)
    parser.add_argument("--platform-fee", type=float, default=5.0)
    parser.add_argument("--gst", type=float, default=0.05)
    args = parser.parse_args()

    menu = demo_menu()
    user = User(member=args.member, first_order=args.first_order)
    config = PricingConfig(
        delivery_fee=args.delivery, platform_fee=args.platform_fee, gst_rate=args.gst
    )

    excluded = [item for item in menu.items if item not in menu.orderable_items(args.time)]
    result = best_cart(menu, user, config, args.budget, now=args.time)
    baseline = best_cart(
        dataclasses.replace(menu, coupons=()), user, config, args.budget, now=args.time
    )

    flags = [f"budget ₹{args.budget}"]
    flags.append("member" if args.member else "guest")
    if args.first_order:
        flags.append("first order")
    if args.time:
        flags.append(f"at {args.time}")
    print(f"{menu.restaurant} — {', '.join(flags)}")
    if excluded:
        print("excluded: " + ", ".join(item.name for item in excluded))
    print()

    if not result.cart.lines:
        print("Nothing fits this budget.")
        return

    for line in result.cart.lines:
        label = _line_label(line)
        qty = f"{line.quantity}x " if line.quantity > 1 else ""
        print(f"  {qty + label:<44} ₹{line.cost:>4}   (pref {line.preference:.2f})")
    print()
    if result.coupon is not None:
        print(f"Coupon: {result.coupon.id} — {result.coupon.description}")
    breakdown = result.breakdown
    print(f"  Subtotal      ₹{breakdown.subtotal:>8.2f}")
    if breakdown.discount:
        print(f"  Discount     -₹{breakdown.discount:>8.2f}")
    print(f"  Delivery      ₹{breakdown.delivery_fee:>8.2f}")
    print(f"  Platform fee  ₹{breakdown.platform_fee:>8.2f}")
    print(f"  GST           ₹{breakdown.tax:>8.2f}")
    print(f"  TOTAL         ₹{breakdown.total:>8.2f}   (preference {result.preference:.2f})")
    if baseline.breakdown.total != breakdown.total or baseline.preference != result.preference:
        print(
            f"\nWithout coupons the best would be ₹{baseline.breakdown.total:.2f} "
            f"for preference {baseline.preference:.2f}."
        )


if __name__ == "__main__":
    main()
