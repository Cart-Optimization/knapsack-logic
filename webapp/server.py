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

import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from cart_optimizer.coupon_ledger import SqliteCouponLedger
from cart_optimizer.models import PricingConfig, User
from cart_optimizer.adapters.swiggy import parse_menu
from cart_optimizer.adapters.swiggy_session import DEFAULT_COUPON_CANDIDATES
from cart_optimizer.discovery import VerifiedCart, propose_candidates
from cart_optimizer.run import _fetch_full_menu, _enrich_menu_detail, _verify_one
from cart_optimizer.swiggy_client import SwiggyClient
from . import oauth

STATIC_DIR = Path(__file__).parent / "static"
COUPON_DB = os.getenv("COUPON_DB", "./data/coupons.db")
CONFIG = PricingConfig(delivery_fee=30, platform_fee=5, gst_rate=0.05)

app = FastAPI(title="Cart Optimizer")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", secrets.token_hex(32)))

# Shared coupon ledger — one DB for ALL users of this backend.
ledger = SqliteCouponLedger(COUPON_DB)

# Server-side session store: cookie holds only an opaque sid; tokens stay here.
_SESSIONS: dict[str, dict] = {}


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
    return RedirectResponse("/")


@app.post("/logout")
def logout(request: Request):
    sid = request.session.pop("sid", None)
    if sid:
        _SESSIONS.pop(sid, None)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"logged_in": bool(_session(request) and "access_token" in _session(request))}


# ── data routes ───────────────────────────────────────────────────────────────

@app.get("/api/addresses")
async def addresses(request: Request):
    token = _token(request)
    async with SwiggyClient(token) as client:
        data = await client.call("get_addresses")
    addrs = data.get("addresses") or (data.get("data") or {}).get("addresses", [])
    return [
        {"id": str(a.get("id") or a.get("address_id")),
         "label": a.get("addressTag") or a.get("flatNo") or a.get("addressLine") or "Address",
         "line": a.get("addressLine", "")}
        for a in addrs
    ]


@app.get("/api/restaurants")
async def restaurants(request: Request, q: str, addressId: str):
    token = _token(request)
    async with SwiggyClient(token) as client:
        data = await client.call("search_restaurants", query=q, addressId=addressId)
    out = []
    for r in data.get("restaurants", []):
        if str(r.get("availabilityStatus", "OPEN")).upper() != "OPEN":
            continue
        out.append({"id": str(r["id"]), "name": r.get("name"),
                    "area": r.get("areaName"), "rating": r.get("avgRating"),
                    "offer": r.get("offer"), "etaMins": r.get("deliveryTimeMinutes")})
    return out[:12]


@app.post("/api/optimize")
async def optimize(request: Request):
    token = _token(request)
    body = await request.json()
    rid = str(body["restaurantId"])
    addr = str(body["addressId"])
    budget = float(body["budget"])
    rname = str(body.get("restaurantName", ""))

    async with SwiggyClient(token) as client:
        raw_menu = await _fetch_full_menu(client, rid, addr)
        search = await _enrich_menu_detail(client, raw_menu, rid, addr)
        menu = parse_menu(raw_menu, search_responses=search, skip_unparseable=True)
        if not rname:
            rname = menu.restaurant
        candidates = propose_candidates(menu, User(), CONFIG, budget, max_candidates=5)

        verified: list[VerifiedCart] = []
        for cart in candidates:
            try:
                bill = await _verify_one(cart, client, rid, rname, addr,
                                         list(DEFAULT_COUPON_CANDIDATES), ledger=ledger)
            except Exception:  # noqa: BLE001
                continue
            if bill.to_pay <= budget:
                verified.append(VerifiedCart(cart, bill))

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


# ── static UI (mounted last so /api/* wins) ───────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text()


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
