"""FastAPI backend for the cart optimizer web UI.

Each visitor logs into their own Swiggy (OAuth/PKCE); we run the
optimize→live-verify pipeline with their token and share discovered coupons
across all users via a SQLite per-branch ledger.

Run locally:
    uvicorn webapp.server:app --reload --port 8000

Env:
    SESSION_SECRET   cookie-signing secret (set in prod; random if unset)
    BASE_URL         public base url, e.g. https://x.onrender.com (else derived)
    SWIGGY_CLIENT_ID pre-registered OAuth client id (else dynamic registration)
    COUPON_DB        sqlite path for the shared ledger (default ./data/coupons.db)
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from cart_optimizer.coupon_ledger import SqliteCouponLedger
from cart_optimizer.models import PricingConfig, User
from cart_optimizer.adapters.swiggy import parse_menu
from cart_optimizer.adapters.swiggy_session import DEFAULT_COUPON_CANDIDATES
from cart_optimizer.discovery import VerifiedCart, propose_candidates, scale_menu_costs
from cart_optimizer.run import _fetch_full_menu, _enrich_menu_detail, _verify_one
from cart_optimizer.swiggy_client import SwiggyClient
from . import oauth

STATIC_DIR = Path(__file__).parent / "static"
COUPON_DB = os.getenv("COUPON_DB", "./data/coupons.db")
SESSION_FILE = Path(os.getenv("SESSION_FILE", "./data/sessions.json"))
CONFIG = PricingConfig(delivery_fee=30, platform_fee=5, gst_rate=0.05)

# Chains we've validated end-to-end; "restaurants our service provides".
SUPPORTED_BRANDS = ["McDonald's", "Burger King", "Starbucks", "Taco Bell", "KFC", "Domino's"]

app = FastAPI(title="Cart Optimizer")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", secrets.token_hex(32)))

# Shared coupon ledger — one DB for ALL users of this backend.
ledger = SqliteCouponLedger(COUPON_DB)


# Server-side session store: cookie holds only an opaque sid; tokens stay here.
# Persisted to disk so logins survive a server restart (single-instance friendly).
def _load_sessions() -> dict[str, dict]:
    try:
        return json.loads(SESSION_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sessions() -> None:
    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(_SESSIONS))
    except OSError:
        pass


_SESSIONS: dict[str, dict] = _load_sessions()


# ── helpers ───────────────────────────────────────────────────────────────────

def _redirect_uri(request: Request) -> str:
    base = os.getenv("BASE_URL") or str(request.base_url).rstrip("/")
    return f"{base}/callback"


def _session(request: Request) -> dict | None:
    sid = request.session.get("sid")
    return _SESSIONS.get(sid) if sid else None


def _token(request: Request) -> str:
    sess = _session(request)
    if not sess or "access_token" not in sess:
        raise HTTPException(status_code=401, detail="not logged in")
    return sess["access_token"]


# ── auth routes ───────────────────────────────────────────────────────────────

@app.get("/login")
def login(request: Request):
    redirect_uri = _redirect_uri(request)
    client_id = oauth.resolve_client_id(redirect_uri)
    auth_url, verifier, state = oauth.start_login(redirect_uri, client_id)
    sid = secrets.token_urlsafe(24)
    request.session["sid"] = sid
    _SESSIONS[sid] = {"pkce": verifier, "state": state, "client_id": client_id}
    _save_sessions()
    return RedirectResponse(auth_url)


@app.get("/callback")
def callback(request: Request, code: str | None = None, state: str | None = None,
             error: str | None = None):
    sess = _session(request)
    if error:
        return HTMLResponse(f"<h3>Login failed: {error}</h3><a href='/'>back</a>", status_code=400)
    if not sess or not code or state != sess.get("state"):
        return HTMLResponse("<h3>Invalid login state.</h3><a href='/'>back</a>", status_code=400)
    try:
        tokens = oauth.exchange_code(
            code, _redirect_uri(request), sess["client_id"], sess["pkce"]
        )
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<h3>Token exchange failed: {e}</h3><a href='/'>back</a>",
                            status_code=400)
    sess.pop("pkce", None)
    sess.update(tokens)
    _save_sessions()
    return RedirectResponse("/")


@app.post("/logout")
def logout(request: Request):
    sid = request.session.pop("sid", None)
    if sid:
        _SESSIONS.pop(sid, None)
        _save_sessions()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"logged_in": bool(_session(request) and "access_token" in _session(request))}


# ── data routes ───────────────────────────────────────────────────────────────

def _as_dict(data):
    """Tolerate a stray JSON string that slipped through."""
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


@app.get("/api/addresses")
async def addresses(request: Request):
    token = _token(request)
    async with SwiggyClient(token) as client:
        data = _as_dict(await client.call("get_addresses"))
    addrs = data.get("addresses") or (data.get("data") or {}).get("addresses", [])
    out = []
    for a in addrs:
        if not isinstance(a, dict):
            continue
        out.append({
            "id": str(a.get("id") or a.get("address_id")),
            "label": a.get("addressTag") or a.get("flatNo") or a.get("addressLine") or "Address",
            "line": a.get("addressLine", ""),
        })
    return out


def _restaurant_row(r: dict) -> dict:
    return {"id": str(r["id"]), "name": r.get("name"),
            "area": r.get("areaName"), "rating": r.get("avgRating"),
            "offer": r.get("offer"), "etaMins": r.get("deliveryTimeMinutes"),
            "distanceKm": r.get("distanceKm")}


@app.get("/api/restaurants")
async def restaurants(request: Request, q: str, addressId: str):
    token = _token(request)
    async with SwiggyClient(token) as client:
        data = _as_dict(await client.call("search_restaurants", query=q, addressId=addressId))
    out = []
    for r in data.get("restaurants", []):
        if not isinstance(r, dict) or str(r.get("availabilityStatus", "OPEN")).upper() != "OPEN":
            continue
        out.append(_restaurant_row(r))
    return out[:12]


@app.get("/api/nearby")
async def nearby(request: Request, addressId: str):
    """Closest OPEN restaurants our service supports, for this delivery address.

    Swiggy search is address-based (no lat/lng tool), so 'near you' = nearest
    branches of our supported chains deliverable to the chosen address, sorted
    by Swiggy's distanceKm."""
    token = _token(request)
    seen: dict[str, dict] = {}
    sem = asyncio.Semaphore(4)
    async with SwiggyClient(token) as client:
        async def one(brand: str):
            async with sem:
                try:
                    return _as_dict(await client.call(
                        "search_restaurants", query=brand, addressId=addressId))
                except Exception:  # noqa: BLE001
                    return {}
        for data in await asyncio.gather(*[one(b) for b in SUPPORTED_BRANDS]):
            for r in data.get("restaurants", []):
                if not isinstance(r, dict):
                    continue
                if str(r.get("availabilityStatus", "OPEN")).upper() != "OPEN":
                    continue
                rid = str(r.get("id"))
                if rid not in seen:                 # keep the closest instance
                    seen[rid] = _restaurant_row(r)
    rows = sorted(seen.values(),
                  key=lambda x: (x.get("distanceKm") is None, x.get("distanceKm") or 1e9))
    return rows[:12]


@app.post("/api/optimize")
async def optimize(request: Request):
    token = _token(request)
    body = await request.json()
    rid = str(body["restaurantId"])
    addr = str(body["addressId"])
    budget = float(body["budget"])
    rname = str(body.get("restaurantName", ""))

    async with SwiggyClient(token) as client:
        menu = await _get_menu_cached(client, rid, addr)
        if not rname:
            rname = menu.restaurant
        candidates = propose_candidates(menu, User(), CONFIG, budget, max_candidates=4)

        # Coupon strategy (minimal calls, only ones with a real chance):
        #   • every cart always tries its auto-SUGGESTED coupon (Swiggy's own best
        #     pick for that cart) + this branch's known-good codes from the ledger;
        #   • a small curated discovery list runs ONLY on the first cart at a branch
        #     we've never seen, to seed the shared ledger. After that: zero blind tries.
        learned = bool(ledger.known(rid))
        seen_keys: set = set()

        async def verify_candidates(carts, allow_discovery):
            out: list[VerifiedCart] = []
            for i, cart in enumerate(carts):
                key = tuple(sorted((l.product_id, l.quantity) for l in cart.lines))
                if not cart.lines or key in seen_keys:
                    continue
                seen_keys.add(key)
                discovery = allow_discovery and i == 0 and not learned
                coupons = list(DEFAULT_COUPON_CANDIDATES) if discovery else []
                try:
                    bill = await _verify_one(cart, client, rid, rname, addr, coupons, ledger=ledger)
                except Exception:  # noqa: BLE001
                    continue
                if bill.to_pay <= budget:
                    out.append(VerifiedCart(cart, bill))
            return out

        verified = await verify_candidates(candidates, allow_discovery=True)

        # Budget calibration: Swiggy item-level discounts make our list prices
        # over-state cost, so the cart can stop well under budget. Learn the real
        # vs listed ratio from the best cart so far, rescale the menu, and
        # re-optimize to fill the actual budget (one extra round).
        if verified:
            top = max(verified, key=lambda v: (v.preference, -v.bill.to_pay))
            est = sum(l.cost for l in top.cart.lines) or 1
            scale = top.bill.item_total / est
            if top.bill.to_pay <= 0.85 * budget and scale < 0.92:
                scaled = scale_menu_costs(menu, scale)
                more = propose_candidates(scaled, User(), CONFIG, budget, max_candidates=4)
                verified += await verify_candidates(more, allow_discovery=False)

    if not verified:
        return JSONResponse({"found": False, "restaurant": rname,
                             "message": f"No cart fits ₹{budget:.0f}."})

    best = max(verified, key=lambda v: (v.preference, -v.bill.to_pay))
    return {
        "found": True,
        "restaurant": rname,
        "preference": round(best.preference, 2),
        "items": [{"name": _line_name(l), "qty": l.quantity, "cost": l.cost}
                  for l in best.cart.lines],
        "bill": {
            "to_pay": best.bill.to_pay,
            "item_total": best.bill.item_total,
            "coupon": best.bill.coupon_code,
            "coupon_discount": best.bill.coupon_discount,
            "free_delivery": best.bill.free_delivery,
            "taxes": best.bill.taxes_and_charges,
            "cod": best.bill.cod_available,
        },
        "branch_known_coupons": ledger.known(rid),
    }


@app.get("/api/coupons/{restaurant_id}")
def branch_coupons(restaurant_id: str):
    """Shared, crowd-sourced coupons known to work at this branch."""
    return {"restaurant_id": restaurant_id, "coupons": ledger.known(restaurant_id)}


def _line_name(line) -> str:
    item = getattr(line, "item", None)
    if item:
        return item.name
    combo = getattr(line, "combo", None)
    return combo.name if combo else line.product_id


# Parsed-menu cache keyed by (restaurant_id, address_id). The menu (and its
# enrichment) doesn't change between optimize requests, so caching it removes the
# pagination + enrichment calls on repeat budgets/visits — a big latency cut.
_MENU_CACHE: dict[tuple[str, str], tuple[float, object]] = {}
_MENU_TTL = 600  # seconds


async def _get_menu_cached(client, rid: str, addr: str):
    key = (rid, addr)
    hit = _MENU_CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    raw_menu = await _fetch_full_menu(client, rid, addr)
    search = await _enrich_menu_detail(client, raw_menu, rid, addr)
    menu = parse_menu(raw_menu, search_responses=search, skip_unparseable=True)
    _MENU_CACHE[key] = (time.time() + _MENU_TTL, menu)
    return menu


# ── static UI (mounted last so /api/* wins) ───────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text()


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
