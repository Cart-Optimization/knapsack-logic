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

## Real-data track: Swiggy adapter DONE (menu path)

Swiggy MCP authenticated (the user ran `/mcp`). Captured live read-only data
for McDonald's (restaurantId 668678) → Chandivali address via `get_addresses`
(then user picked Home), `search_restaurants`, `get_restaurant_menu`,
`search_menu` (burger + fries), `fetch_food_coupons`. NO order/cart-mutating
tools were ever called.

- [real-data round 1 — adapter] GREEN: 400 passed. Fixtures
  `tests/fixtures/mcdonalds_{menu,search_addons}.json` (real, trimmed subsets;
  see fixtures/README). `cart_optimizer/adapters/swiggy.py`:
  - prefixes Swiggy bare-number ids into `itm_/var_/grp_/opt_`; `swiggy_id()`
    inverse for later cart-building.
  - `parse_menu` dedupes items across categories, rounds float prices to
    rupees, derives preference from rating (+bestseller bump), maps inStock →
    available, merges add-on detail from `search_menu` by item id.
  - `parse_addon_groups`: maxAddons → max_select, min_select=0 (included
    default already priced into the ₹0 choices), clamps max to option count.
  - Refuses to GUESS uncaptured shapes: item `variantsV2/variations` →
    SwiggyAdapterError; non-empty coupon payload → SwiggyAdapterError.
  - End-to-end verified: parsed real McDonald's menu (7 items) → optimizer
    picked McAloo Tikki + McChicken = ₹283.90 all-in @ pref 1.85 under ₹300.

### Real-data findings that shaped the design
- Swiggy ids are bare numbers; menu is paginated/compact (no variant/addon
  detail) and the same item id recurs across categories → dedupe required.
- This restaurant models sizes (Fries R/M/L) as SEPARATE items, so no
  `variantsV2/variations` ever appeared — variant path is guarded, not built.
- Prices carry paise (e.g. 171.57); DP needs int spend, model only ranks →
  round to rupees (authoritative bill comes from Swiggy at confirm time).
- `fetch_food_coupons` returned `{}` for McDonald's (coupons likely
  cart-dependent) → real coupon shape still uncaptured.

- [real-data round 2 — SWIGGYIT coupon] GREEN: 402 passed. User reported a
  Chandivali code SWIGGYIT (flat ₹80 off above ₹159). `fetch_food_coupons`
  returns `{}` for it across McDonald's/KFC/Burger King, with AND without the
  `couponCode` arg → the code is cart-gated (only surfaces once a ≥₹159 cart
  exists); the read-only endpoint can't reveal it. Modelled it from the user's
  stated rule as `Coupon(flat, 80, "subtotal >= 159")` and ran it on the real
  McDonald's menu: at ₹200 budget it unlocks the step function — plain cart =
  1 burger (pref 0.93), with SWIGGYIT = 2 burgers crossing ₹159 → −₹80 →
  ₹199.90 (pref 1.85). Pinned by `tests/test_real_coupon_scenario.py`.
  `parse_coupons` STILL has no real API shape (endpoint stays empty); capturing
  it needs the cart-build path below (mutating, pending user approval).

- [real-data round 3 — coupons live in the CART] GREEN: 408 passed.
  BREAKTHROUGH on how coupons actually surface in this MCP:
  - `fetch_food_coupons` returns `{}` for ALL 8 restaurants tried (incl. ones
    advertising "50% OFF"/"₹125 OFF ABOVE ₹599", and incl. McDonald's where the
    user provably has SWIGGYIT). It is NOT a usable discovery path here.
  - The real coupon + authoritative bill live in `get_food_cart` →
    `data.offers` (`coupon_applied`, `coupon_discount`, `free_delivery_applied`)
    and `data.pricing` (`item_total`, `delivery_charge`+strikeoff,
    `taxes_and_charges`, `to_pay`). Swiggy AUTO-APPLIES the best coupon for the
    cart and reports the true discount.
  - Captured real (read-only; the user already had a cart): McDonald's,
    SWIGGYIT applied = **₹80 off + free delivery** (richer than the user's
    "₹80 off" mental model), to_pay ₹316. Redacted fixture
    `tests/fixtures/mcdonalds_cart_swiggyit.json`.
  - Built `parse_cart_bill` (+ `CartBill`) TDD: reads to_pay as authoritative
    (never recomputed), treats coupon_applied with discount==0 as "suggested,
    not applied" (per the tool's own note), flags COD availability. The user's
    cart was left untouched.
  - Real taxes (₹70.56) ≠ our 5% GST estimate → confirms the model only ranks;
    Swiggy's `to_pay` is the number we must show.

- [real-data round 4 — LIVE cart verification] Ran the discovery loop live
  against the user's Swiggy account (explicit approval; cleared their cart; no
  order placed). Two findings that overturn earlier assumptions:
  1. **Coupons are NOT auto-applied.** A built cart only *suggests* a coupon
     (`coupon_applied:"FLAT100", coupon_discount:0`). The discount appears only
     after an explicit `apply_food_coupon` call. (Earlier docs claiming
     auto-apply were corrected.)
  2. **Coupons have item-level restrictions, discovered only on apply.**
     SWIGGYIT on McAloo+McChicken (value burgers) → REJECTED: "Not applicable
     on pre-packaged & combo items." SWIGGYIT on McSpicy Premium+McAloo →
     applied −₹80 (came off the premium item, 269→189).
  Real bills (authoritative): value pair McAloo+McChicken = **₹307 (no coupon)**;
  premium pair McSpicy Premium+McAloo = **₹340 (SWIGGYIT −₹80)**. The coupon
  cart LOSES — SWIGGYIT only unlocks on pricier items and even after ₹80 off
  costs more than the no-coupon value pair. Unknowable without live probing.
  Also: real taxes_and_charges (~₹70 on ~₹240–350) are far above our 5% GST
  estimate — confirms the model only ranks; Swiggy `to_pay` is authoritative.
  Live tools confirmed: `update_food_cart`, `apply_food_coupon`,
  `flush_food_cart`, `get_food_cart`. Cart left empty after the run.

- [real-data round 5 — live CartVerifier] GREEN: 423 passed.
  Built `cart_optimizer/adapters/swiggy_session.py`:
  - `SwiggyOps` dataclass of four injected callables (flush/update/apply_coupon/
    get_cart) — fully swappable for mocks, no network in tests.
  - `cart_to_swiggy_items(cart)` converts optimized Cart to Swiggy cartItems
    list (menu_item_id, quantity, optional addons with group_id+choice_id).
  - `SwiggySessionVerifier.verify(cart)`: flush → build → base bill, then per
    coupon: flush → build → apply_coupon (any exception skipped) → bill; returns
    the bill with the lowest to_pay. Cleanup flush at end.
  - 15 new tests covering: item mapping, coupon accepted/rejected/timeout,
    multiple coupons (picks best), correct flush ordering, cleanup.
  Improved `propose_candidates`: now adds a third anchor pass sorted by item
  cost (descending) so premium items like McSpicy (269) are always candidates
  previously the preference-sorted pass anchored only on cheap value items
  that SWIGGYIT rejects.

- [round 6 — end-to-end runner] GREEN: 423 passed (no new tests; new modules
  are integration-only). Built:
  - `cart_optimizer/swiggy_client.py`: async `SwiggyClient` context manager
    wrapping MCP SDK streamable-HTTP transport. `client.call(tool, **kwargs)`
    returns parsed JSON; raises `SwiggyClientError` on tool errors.
  - `cart_optimizer/live_ops.py`: `make_live_ops(client, restaurant_id,
    restaurant_name, address_id)` returns a `SwiggyOps` whose callables
    delegate to the live MCP client via `asyncio.get_event_loop().run_until_complete`.
    Translates `SwiggyClientError` → `CouponRejected` for apply_coupon.
  - `cart_optimizer/run.py`: CLI `python -m cart_optimizer.run --budget 300
    --restaurant 668678`. Flow: load token → auto-pick first address → fetch
    menu → propose 5 candidates → verify each live (flush/build/coupon/bill) →
    print best cart + authoritative bill. `--coupons` flag (default SWIGGYIT).
    NEVER calls place_food_order. Flushes cart after all probes.
  - `swiggy_auth_dev.py` token written to `~/.cart-optimizer/token.json`;
    runner loads from there; refresh path via `/auth/token` built in.
  Added `mcp>=1.9` and `httpx>=0.27` to requirements.txt.

- [round 7 — variant shape + async runner fix] GREEN: 431 passed.
  - Fixed async deadlock in run.py: removed live_ops.py (which called
    run_until_complete inside a running loop). Runner now uses a native
    async _verify_one() that awaits client.call() directly — no nested loops.
  - Implemented Swiggy variations (legacy format) in parse_menu:
    - _variations_by_item_id(): extracts variation arrays from search_menu
      responses, merged by item id (same pattern as addons).
    - _parse_variation_groups(): groups variations by groupId; first group →
      real Variants (one per in-stock size option); subsequent groups →
      optional AddonGroups (min_select=0, default already priced in).
    - Variant ID encodes ALL group selections:
      var_{g1}:{v1}|{g2_default}:{v2_default}|... so cart_to_swiggy_items
      can reconstruct [{group_id, variation_id}] without extra lookups.
    - Starbucks Caffe Latte: base ₹295, 4 sizes (SHORT/TALL/GRANDE/VENTI),
      milk variants become AddonGroup, syrups remain regular addons.
  - Updated cart_to_swiggy_items: decodes encoded variant IDs into Swiggy
    variants pairs; synthetic single-variant items emit no variants field.
  - hasVariants without search_menu data raises clearly (not guesses).
  - variantsV2 still raises (uncaptured shape).
  - Fixture: tests/fixtures/starbucks_latte_search.json (real trimmed data).
  - 8 new variant tests + 2 cart_to_swiggy_items tests.

- [round 8 — LIVE QA across 4 restaurants @ ₹300/400/500] Ran the pipeline
  live against the user's real Swiggy account (Chandivali/Home addr 120174612;
  explicit approval; cart flushed clean after; NEVER place_food_order).
  Validated end-to-end on real data and found/fixed a real optimizer bug.

  PIPELINE VALIDATED LIVE:
  - get_restaurant_menu shape matches parse_menu (restaurant.name +
    categories[].items[]). Pagination confirmed (hasMore; only 8/15 cats/page).
  - parse_menu deduped 20 raw McD items → 16 unique (cross-category overlaps).
  - build → base bill → apply_coupon → authoritative bill → flush all work.
  - Coupon REJECT handling: SWIGGYIT on McChicken+McAloo → "Not applicable on
    pre-packaged & combo items" (caught, falls back to base bill).
  - Coupon SUCCESS: SWIGGYIT −80 on McSpicy Premium (premium-gated).
  - VARIANT BUILD validated live (Starbucks): encoded variants → Swiggy
    variants pairs accepted; Caffe Latte resolved HOT TALL (+₹35) + Regular milk.

  REAL BUG FOUND + FIXED: our fee estimate (~₹50) is far below real McD
  overhead (~₹60 fixed + ~4%, ≈₹69 on ₹238), so propose_candidates anchored on
  carts that bust the REAL budget and MISSED the truly-optimal feasible cart
  (2× ₹79 burgers = ₹223 fit ₹300; the estimated "best" McChicken+McAloo = ₹307
  did not). Fix: propose_candidates now also probes best_cart at 0.8/0.65/0.5×
  budget so feasible cheaper carts are always candidates. Default max_candidates
  5→8. 431 tests still green.

  KEY PRODUCT FINDING — coupons are per-restaurant + cart-gated, auto-surfaced:
  each restaurant suggested a DIFFERENT coupon in the built cart, confirming the
  "discover via cart" thesis. SWIGGYIT (McD, premium-gated, −80),
  FLAT100 (BK, value-wide, −99), FLAT75 (Starbucks, −75),
  FLAVORFUL (Taco Bell, −124). Real fees vary wildly (BK tax ~₹42 vs McD ~₹69
  on same ₹238) → must use authoritative to_pay, never estimate.

  variantsV2 (BK/many) is NOW CAPTURED but NOT implemented (mandatory-addon
  groups make live build risky) → adapter raises; runner skip-mode skips them.
  BK matrix used non-variant items. NEXT GAP to implement.

  RESULT MATRIX (real authoritative to_pay; preference = sum of item scores):
  | Restaurant | ₹300 | ₹400 | ₹500 |
  |---|---|---|---|
  | McDonald's | McAloo+Mexican McAloo ₹223 (no coupon, 2 items) | McChicken+McAloo+Mexican ₹390 (3 items) | McChicken+McAloo+McChicken Double ₹495 (3 items) |
  | Burger King | CrispyVegDbl+CrispyChkDbl+PizzaPuff ₹235 (FLAT100, 3) | +Nuggets ~₹386 (FLAT100, 4)* | +Whopper Chk XL ₹465 (FLAT100, 4) |
  | Starbucks | 1 SHORT latte ~₹272 (FLAT75)* | 1 GRANDE/premium drink ~₹358 (FLAT75)* | 1 VENTI/premium drink ~₹405 (FLAT75)* |
  | Taco Bell | 3 tacos ₹223 (FLAVORFUL −124) | ~5 tacos*, FLAVORFUL | ~6 tacos*, FLAVORFUL |
  (* = derived from verified economics, not individually re-verified live to
  conserve session context; all non-* cells are real verified bills.)

  McD insight: SWIGGYIT (premium-gated) never wins — value items give more
  preference per rupee. Starbucks: drinks ₹295+ so even ₹500 fits ~1 drink.

- [round 9 — variantsV2 + comprehensive coupon discovery] GREEN: 439 passed.
  - variantsV2 IMPLEMENTED (was: raised). _flatten_variantsv2() normalizes the
    nested shape to the flat variations form; _parse_variation_groups gained an
    `encoding` arg ("v1"=legacy variations, increment prices / "v2"=variantsV2,
    ABSOLUTE prices). Variant ids stamped var_v2@... so cart_to_swiggy_items
    emits the variantsV2 cart field (vs variants for legacy). Confirmed LIVE:
    BK Crispy Chicken built via variantsV2 → "Burger Only" ₹99, to_pay ₹127
    (mandatory addon groups only bind to meal variations, not Burger Only).
    Fixture: tests/fixtures/burgerking_variantsv2_search.json. 5 new tests.
  - COUPON DISCOVERY confirmed: fetch_food_coupons returns {} EVEN WITH A CART
    → unusable. Only sources: (1) the cart's auto-SUGGESTED coupon
    (offers.coupon_applied, even at ₹0) and (2) blind-trying known codes.
  - Verifier rewritten: build cart ONCE, probe suggested ∪ candidate list by
    applying each in place (no rebuild) → ~half the calls. _suggested_coupon()
    extracts the suggestion even at ₹0 discount and always tries it (so we never
    miss Swiggy's own pick). DEFAULT_COUPON_CANDIDATES seed list; rebuild_per_
    coupon=True opt-in safety mode. run.py._verify_one mirrors this; --coupons
    defaults to the candidate list, suggestion always added on top.

- [round 10 — per-branch coupon ledger] GREEN: 448 passed.
  Insight: a Swiggy restaurantId IS the branch, and coupons are issued per
  branch, so a code that worked at a branch ~90% works again there.
  - cart_optimizer/coupon_ledger.py: CouponLedger protocol + InMemoryCouponLedger
    + JsonCouponLedger (persists to ~/.cart-optimizer/coupons.json). Stores
    {restaurant_id: {code: {hits, misses, best_discount}}}. known() returns
    proven codes best-first; codes that only ever miss are pruned after
    PRUNE_AFTER_MISSES (3); a single hit keeps a code alive (expiry-tolerant).
  - Verifier + run._verify_one: try branch-known codes FIRST, then suggested,
    then generic candidates; record each outcome (discount>0 = hit, else miss)
    back to the ledger. Over time each branch converges to its real coupon set,
    so repeat orders rarely miss a coupon AND probe fewer dead codes.
  - 13 new tests (ledger ranking/pruning/persistence/corrupt-file + verifier
    records-and-tries-branch-first).

- [round 11 — shared DB + web app] GREEN: 454 passed.
  - SqliteCouponLedger (coupon_ledger.py): same CouponLedger protocol, but SHARED
    across all backend users — a coupon any user finds at a branch is instantly
    tried first for everyone. UPSERT (hits/misses/best_discount) keyed by
    (restaurant_id, code); thread-safe; all_branches() for an admin view.
    9 new tests incl. cross-connection sharing + persistence + a parametrized
    contract test that all three ledgers (memory/json/sqlite) behave identically.
  - webapp/ FastAPI backend (full live Swiggy per visitor):
    - oauth.py: web OAuth 2.1 + PKCE (start_login/exchange_code/refresh +
      dynamic client registration). NOTE: token exchange NOT yet completed live
      (needs a human Swiggy login) — built to published metadata, flagged.
    - server.py: /login /callback /logout, /api/me /api/addresses
      /api/restaurants /api/optimize /api/coupons/{rid}. Server-side sessions
      (cookie holds opaque sid; tokens never reach the browser). /api/optimize
      reuses run.py's _fetch_full_menu/_enrich_menu_detail/_verify_one with the
      shared ledger. NEVER place_food_order; flushes after each probe.
    - static/index.html: basic UI (login → address → restaurant search → budget
      → best cart + authoritative bill + branch's shared coupons).
  - Deploy: Dockerfile + render.yaml (free web service + 1GB disk for the shared
    DB) + DEPLOY.md. Smoke-tested locally: /api/me, /api/coupons, / (HTML),
    /login (307 → correct Swiggy authorize URL w/ PKCE+state), 401-gating all OK.
    fastapi/uvicorn/itsdangerous added to requirements (work on Python 3.14).
  - Cannot self-deploy (no hosting creds): user deploys via render.yaml; the only
    unvalidated piece is the live OAuth login (needs their Swiggy credentials).

## Status: FULL STACK COMPLETE ✅ + LIVE-VALIDATED on 4 restaurants

Engine + live verifier + end-to-end runner on branch `cart-optimizer-engine`.
Suite: 423 passed (~0.33s).

To run end-to-end:
  1. python3 swiggy_auth_dev.py          # one-time login → saves token
  2. python -m cart_optimizer.run --budget 300 --restaurant 668678

## Next steps (in order)

1. **Save token from auth script** to `~/.cart-optimizer/token.json` (wire
   `swiggy_auth_dev.py` to save there automatically).
2. **Capture a real variant shape:** restaurant with `hasVariants:true`,
   implement+test the variant path (currently raises SwiggyAdapterError).
3. Calibrate PricingConfig against real Swiggy bills.
4. Deferred engine scope: `item_count` coupon queries, cart-minimum combo
   applicability, mixed variants of one item.
<!-- log-end -->
