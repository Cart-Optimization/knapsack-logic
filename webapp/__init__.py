"""Web app: a basic multi-user UI over the cart optimizer.

Each visitor logs into their own Swiggy account (OAuth 2.1 + PKCE); the backend
runs the optimize→live-verify pipeline on their behalf and shares discovered
coupons across all users via a SQLite-backed per-branch ledger.

SAFETY: never calls place_food_order; the live cart is flushed after every probe.
"""
