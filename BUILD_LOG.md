# Build Log — cart_optimizer

Purpose: crash-resumable record of progress. If a session dies, read this file
top-to-bottom; the last log entry says exactly where things stand and what to
do next.

## Project shape (decided in design discussion)

- **Goal (part 1):** exact coupon-aware cart optimizer. Given one restaurant
  menu + applicable offers + user + budget, return the provably best cart:
  max total preference, ties broken by lower final price, final price
  (subtotal − discount + delivery + platform fee + GST) ≤ budget.
- **Algorithm:** multiple-choice knapsack DP over *exact* spend levels, then a
  coupon layer that evaluates each coupon as a function of spend. Scoped
  coupons (`applies_to`) use a two-knapsack decomposition (in-scope DP ×
  rest DP, cross-joined). This captures the FLAT100 step function (adding an
  item can *lower* the final price by crossing a threshold).
- **Verification:** a brute-force oracle enumerates every cart × coupon and is
  asserted equal to the DP on ~120 random menus (property test). Both solvers
  share the same money math (`pricing.price_amounts`) so they cannot diverge
  on pricing rules — only on search strategy.
- **v1 scope:** items with choose-one variants; coupons flat / percent /
  free_delivery, optionally scoped via `applies_to`; delivery + platform fee +
  GST; availability flags + time windows. NOT in v1: add-ons, combos,
  quantities > 1.
- **Coupon query vocabulary** (validated at construction time, fail-fast):
  `subtotal`, `select_subtotal`, `user.member`, `user.first_order`,
  JSON literals `true/false/null`. `item_count` deliberately unsupported
  (would need an extra DP dimension).
- **Money:** item costs are int rupees; totals rounded to 2 dp.
- **Real-data track:** Swiggy MCP server is remote
  (`https://mcp.swiggy.com/food`, OAuth 2.1 + PKCE) — the user must connect it
  (`claude mcp add --transport http swiggy-food https://mcp.swiggy.com/food`).
  Adapter (`cart_optimizer/adapters/swiggy.py`) will be written against a
  captured live menu response. NOT done yet — blocked on connector.
- Root-level `knapsack.py` is the old prototype, superseded by this package.

## Module map

| File | Role |
|---|---|
| `cart_optimizer/safe_eval.py` | sandboxed AST evaluator for coupon query strings |
| `cart_optimizer/models.py` | Item/Variant/Coupon/Cart/Menu/User/PricingConfig + `Menu.from_dict` |
| `cart_optimizer/pricing.py` | shared money math: eligibility, discount, breakdown |
| `cart_optimizer/optimizer.py` | the exact DP solver (ships) |
| `cart_optimizer/brute_force.py` | enumeration oracle (tests only) |
| `cart_optimizer/mock_data.py` | spec-flavoured demo menu |
| `cart_optimizer/demo.py` | CLI demo: `python -m cart_optimizer.demo` |
| `tests/` | per-module tests + DP==brute-force property test |

## Environment

- Python: (see first log entry)
- pytest 9.0.3, pytest-randomly 4.1.0 in `./venv`
- Branch: `cart-optimizer-engine`

## Log

- [setup] venv ready, package skeleton (`cart_optimizer/`, `tests/`),
  `pytest.ini`, `requirements.txt`, `.gitignore` written.
- [round A — safe_eval] GREEN: 45 passed. Sandboxed AST evaluator with
  whitelist, JSON literals, short-circuit and/or, chained comparisons,
  parse-time vocabulary validation (`validate_expression`). Python 3.14.4.
- [round B — models] GREEN: 97 passed cumulative. Item/Variant/Coupon/Cart/
  Menu/User/PricingConfig with fail-fast validation (incl. coupon query
  vocabulary), wrap-around time windows, `Menu.from_dict` for the normalized
  JSON shape (variant map, int shorthand, bare-cost synthetic variant).
- [round C — pricing] GREEN: 13 passed (110 cumulative). Amount-level pricing
  (`price_amounts`) + cart wrapper, eligibility via safe_eval, GST on
  discounted item total, free_delivery waives the fee, 2dp rounding.
- [round D — brute_force] GREEN: 7 passed (117 cumulative). Oracle enumerates
  all carts × coupons via shared pricing; hand-verified cases incl. coupon
  threshold unlock, member free-delivery, scoped percent, tie-break.
- [round E — optimizer] GREEN: 11 passed (128 cumulative). Exact DP:
  multiple-choice knapsack over exact spend + per-spend coupon evaluation;
  scoped coupons via two-knapsack decomposition; spend cap derived from max
  possible coupon clawback. Step-function case verified (budget 120: solver
  adds a ₹60 item to unlock FLAT100 → ₹210 cart prices at ₹110).
- [round F — equivalence] GREEN: 120 random menus, DP == brute force on
  (preference, total), plus structural validity of both results. Extra
  one-off fuzz: 1000/1000 additional random scenarios matched.
- [round G — packaging] GREEN: full suite 250 passed in 0.15s. Added
  mock_data (7 items, 3 offers incl. unavailable + breakfast-window items),
  demo CLI (`python -m cart_optimizer.demo`), package exports, adapters stub
  with capture instructions, README. Demo verified: member/₹300 → FLAT100
  cart ₹218.80 @ pref 1.70 (vs ₹263.95 @ 1.30 couponless); guest/₹150 →
  ₹199 Margherita lands at ₹137.95 via FLAT100 (couponless same price only
  reaches pref 0.70) — the step-function win, end to end.

### Checkpoint: PART 1 (v1) ENGINE COMPLETE — 250 passed.
(Superseded by the v2 status at the bottom of this log; kept for history.)

## v2: full spec data model

Scope: add-on groups (`grp_` min/max + `opt_` options with own cost/pref),
combos (`cmb_` with cost, own preference, display composition, user-status
applicability), per-line quantities (opt-in via `max_quantity`, default 1 —
v1 behavior preserved). Design: a new `choices.py` enumerates every valid
order line per product (item config = variant × addon-selection × qty;
combo = qty); BOTH solvers consume the same per-product choice lists and
pick at most one line per product, so the DP stays a multiple-choice
knapsack and equivalence keeps verifying the search. Deliberately deferred:
cart-minimum combo applicability and `item_count` coupon queries (rejected
at validation, documented), mixed variants of the same item in one cart.

- [v2 plan] Swiggy MCP still not connected (ToolSearch: no swiggy tools) —
  real-data track remains blocked on user running `claude mcp add`.
- [v2 round 1 — models] GREEN: 286 passed (all 250 v1 tests untouched).
  AddonOption/AddonGroup (min/max bounds, unique ids across groups), Combo
  (user-status applicability only; cart-state rejected), ItemLine
  (variant+addons+quantity with full config validation; CartLine alias) and
  ComboLine, product-id based Cart, Menu.combos + orderable_combos +
  from_dict for addons/max_quantity/combos, coupon scopes accept cmb_ ids.
  Preference sums switched to line level (identical for v1 lines).
- [v2 round 2 — choices] GREEN. `choices.py`: product_lines() expands an
  item into variant × addon-selection × quantity lines (combinations honor
  group min/max), combo into quantity lines; menu_choices() filters by
  availability/time/user-applicability and groups per product. Explosion
  guard MAX_LINES_PER_PRODUCT=10_000.
- [v2 round 3 — solvers] GREEN. Rewrote BOTH solvers onto menu_choices: DP
  is now a multiple-choice knapsack over per-product line lists (variants,
  addons, qty, combos all ride the same machinery); brute force enumerates
  {skip}∪lines per product. Hand cases in test_solvers_v2 cover mandatory/
  optional addons, qty filling budget, qty/addon crossing a coupon
  threshold (step function generalized), combo-vs-items tradeoff, combo
  applicability, scoped coupon on a combo. One test expectation corrected
  (combo composes WITH a separately-added soda → that's the true optimum;
  oracle confirmed).
- [v2 round 4 — equivalence] GREEN: 383 passed. Extended random_scenario to
  generate addons (~55%), combos (~66%), quantities (~58%) with a
  brute-force-size trim (cap 60k carts); made assert_valid_result and the
  equivalence detail-string line-type-aware. Property test now 200 seeds.
  Off-line fuzz: **2000/2000 matched in 1.8s**. Enriched mock_data (pizza
  addons, 3x drink cap, two combos incl. first-order welcome) + line-aware
  demo renderer; exports + version bumped to 0.2.0.

## Status: v1 + v2 ENGINE COMPLETE ✅

Full spec data model implemented and proven exact. Branch
`cart-optimizer-engine`, uncommitted. Suite: `venv/bin/python -m pytest` →
383 passed (~0.3s). DP == brute-force oracle on 200 in-suite + 2000 off-line
random menus spanning the whole feature surface.

## Next steps (in order)

1. **Real-data track (blocked on user):** connect the Swiggy MCP server —
   `claude mcp add --transport http swiggy-food https://mcp.swiggy.com/food`
   then authenticate via /mcp. Once connected: list tools, fetch addresses →
   search restaurants → pull one live menu, save raw JSON to
   `tests/fixtures/`, write `cart_optimizer/adapters/swiggy.py` + tests.
   NEVER call order-placement tools (COD-only, non-cancellable).
2. Calibrate PricingConfig against a real Swiggy bill (read the bill of the
   built cart via MCP; our model only ranks).
3. Remaining deferred scope if needed: `item_count` coupon queries and
   cart-minimum combo applicability (each adds a DP dimension), mixed
   variants of one item in a cart.
<!-- log-end -->
