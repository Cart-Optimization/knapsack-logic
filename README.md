# cart-optimization

Engine for a mobile assistant that builds the best-value Swiggy Food cart:
given one restaurant's menu, the currently applicable coupons, the user's
taste preferences and a budget, return the **provably optimal** cart —
maximum total preference, ties broken by lower price, with the final amount
(subtotal − best coupon + delivery + platform fee + GST) within budget.

## Why this isn't plain knapsack

Coupons break the knapsack assumption that adding an item only costs more.
With `FLAT100 (₹100 off above ₹199)`, adding a ₹60 side to a ₹150 cart drops
the final price from ₹150 to ₹110. The optimizer therefore runs a
multiple-choice knapsack DP over *exact* spend levels and then evaluates every
coupon as a function of spend; scoped coupons (e.g. "30% off pizzas") get a
two-knapsack decomposition (in-scope × rest). Exact, no heuristics — menus
and budgets are small enough that this is instant.

Each product (item or combo) is first expanded into its valid order
*lines* — variant × add-on selection × quantity (`choices.py`) — and the DP
picks at most one line per product. So add-ons, quantities, and combos all
ride on the same multiple-choice-knapsack machinery, and the step-function
trick generalizes: adding cheese to a pizza can push it past a scoped
coupon's threshold and end up *cheaper*.

Correctness is enforced by a brute-force oracle: tests assert the DP equals
full enumeration on hundreds of random menus (`tests/test_equivalence.py`).
Both solvers share one pricing module so they cannot diverge on money math.

## Layout

| Path | Role |
|---|---|
| `cart_optimizer/safe_eval.py` | sandboxed AST evaluator for coupon query strings |
| `cart_optimizer/models.py` | Item / Variant / AddonGroup / AddonOption / Combo / Coupon / Cart / Menu / User / PricingConfig |
| `cart_optimizer/choices.py` | expand each product into its valid order lines |
| `cart_optimizer/pricing.py` | shared money math (eligibility, discount, bill breakdown) |
| `cart_optimizer/optimizer.py` | the exact coupon-aware DP (ships) |
| `cart_optimizer/brute_force.py` | enumeration oracle (tests only) |
| `cart_optimizer/mock_data.py` / `demo.py` | demo menu + CLI |
| `cart_optimizer/adapters/` | (next) Swiggy MCP → normalized schema |
| `knapsack.py` | old prototype, superseded by the package |

## Run

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python -m pytest                      # 383 tests
venv/bin/python -m cart_optimizer.demo --budget 400 --member --time 13:00
venv/bin/python -m cart_optimizer.demo --budget 300 --first-order --time 13:00
```

## Scope

Supported: items with choose-one variants; **add-on groups** (`grp_`/`opt_`
with per-group min/max selection and per-option cost + preference);
**per-item/per-combo quantities** (opt-in via `max_quantity`, default 1);
**combos** (`cmb_` priced as opaque bundles, with user-status applicability —
membership / first-order); flat / percent / free-delivery coupons, optionally
scoped via `applies_to` (items *or* combos); delivery + platform fee + GST;
availability flags and time windows (midnight wrap supported).

Deliberately deferred (rejected at validation, not silently ignored):
`item_count` coupon conditions and cart-minimum combo applicability (both need
an extra DP dimension), and mixing several variants of the *same* item in one
cart.

## Real data (next step)

The Swiggy MCP Food server is remote (`https://mcp.swiggy.com/food`, OAuth):

```bash
claude mcp add --transport http swiggy-food https://mcp.swiggy.com/food
```

Then capture one live menu response, write `adapters/swiggy.py` against it,
and run the optimizer on the real menu. **Never call order-placement tools
during testing — the server is COD-only and orders cannot be cancelled.**
The internal fee model only ranks carts; the authoritative bill is read back
from Swiggy before anything is shown for confirmation.
