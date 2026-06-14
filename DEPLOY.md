# Deploying the Cart Optimizer web app

A FastAPI backend + static UI. Each visitor logs into their own Swiggy account
(OAuth 2.1 + PKCE); the backend runs the optimize→live-verify pipeline and shares
discovered coupons across all users via a SQLite per-branch ledger.

> **Safety:** the app never calls `place_food_order`. It builds carts, applies
> coupons to read the real bill, and flushes the cart after every probe.

## Run locally

```bash
./venv/bin/uvicorn webapp.server:app --reload --port 8000
# open http://localhost:8000
```

For the OAuth redirect to work locally, the redirect URI is
`http://localhost:8000/callback` (derived from the request automatically).

## Deploy to Render (recommended — free tier, persistent disk)

1. Push this repo to GitHub (already on branch `cart-optimizer-engine`).
2. In Render → **New → Blueprint**, point it at the repo. It reads `render.yaml`
   and creates the Docker web service + a 1 GB disk for the shared coupon DB.
3. First deploy gives you a URL like `https://cart-optimizer-xxxx.onrender.com`.
4. Set the **`BASE_URL`** env var to that exact https URL and redeploy (so the
   OAuth `redirect_uri` matches). Optionally set `SWIGGY_CLIENT_ID` (below).
5. Open the URL and click **Login with Swiggy**.

`SESSION_SECRET` is auto-generated. `COUPON_DB=/data/coupons.db` lives on the disk
so the shared ledger survives restarts.

### Vercel / Fly / Railway
Any Docker host works — point it at the `Dockerfile`, set `BASE_URL` to the public
URL, and mount a volume at `/data` (or accept that the coupon DB resets on redeploy).

## ⚠️ The one thing that needs your live test: Swiggy OAuth

The OAuth flow is built strictly to Swiggy's published metadata
(`/.well-known/oauth-authorization-server`), but a **real token exchange has not
been completed end-to-end** — that needs a human Swiggy login, which can't be
automated here. Two things to verify on first login:

1. **Dynamic client registration.** By default the app tries RFC 7591 dynamic
   registration at `https://mcp.swiggy.com/auth/register` with your deploy's
   `redirect_uri`. If Swiggy rejects it, you'll see a login error. In that case,
   register a client out-of-band (or reuse the one Claude's `/mcp` uses) and set
   `SWIGGY_CLIENT_ID`.
2. **redirect_uri match.** It must exactly equal `${BASE_URL}/callback`. Mismatch
   → Swiggy returns an error on the callback. Fix `BASE_URL` and redeploy.

If login fails, the rest of the app (menu/optimize/verify) can't run, because it
needs the user's token. Everything *after* a successful login is already
validated against live Swiggy in earlier testing.

## What's shared vs per-user
- **Per-user:** the OAuth token (server-side session, keyed by an opaque cookie
  `sid`; tokens never go to the browser).
- **Shared:** the coupon ledger (`/data/coupons.db`). A coupon any user finds at
  a branch is immediately tried first for every other user at that branch.
