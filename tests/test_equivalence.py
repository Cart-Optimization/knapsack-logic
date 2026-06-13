"""Property test: the DP optimizer must match the brute-force oracle.

The oracle enumerates every cart and is obviously correct; the DP is fast and
subtle. On many small random menus — spanning variants, addon groups,
quantities, and combos — both must report the same (preference, total)
optimum. Any divergence is a bug in the DP's search or coupon layer.
"""

import math
import random

import pytest

from cart_optimizer.brute_force import best_cart_brute_force
from cart_optimizer.optimizer import best_cart
from tests.helpers import assert_valid_result, random_scenario


def _describe(line):
    """Compact label for either line type, including addons and quantity."""
    if hasattr(line, "variant"):
        addons = "+".join(o.id for o in line.addons)
        body = line.variant.id + (f"[{addons}]" if addons else "")
    else:
        body = line.combo.id
    return f"{body}x{line.quantity}"


def _summary(tag, result):
    lines = [_describe(line) for line in result.cart.lines]
    return (
        f"{tag}: pref={result.preference} total={result.breakdown.total} "
        f"coupon={result.breakdown.coupon_id} lines={lines}"
    )


@pytest.mark.parametrize("seed", range(200))
def test_optimizer_matches_brute_force(seed):
    rng = random.Random(1000 + seed)
    menu, user, config, budget = random_scenario(rng)

    dp = best_cart(menu, user, config, budget)
    bf = best_cart_brute_force(menu, user, config, budget)

    detail = (
        f"seed={seed} budget={budget}\nuser={user}\nconfig={config}\n"
        f"menu={menu}\n{_summary('dp', dp)}\n{_summary('bf', bf)}"
    )
    assert math.isclose(dp.preference, bf.preference, rel_tol=0, abs_tol=1e-9), detail
    assert math.isclose(
        dp.breakdown.total, bf.breakdown.total, rel_tol=0, abs_tol=1e-9
    ), detail
    assert_valid_result(dp, menu, user, config, budget)
    assert_valid_result(bf, menu, user, config, budget)
