"""Swiggy OAuth 2.1 + PKCE — web (server-side) flow.

Endpoints discovered from https://mcp.swiggy.com/.well-known/oauth-authorization-server.
The flow:
  1. start_login()  → (auth_url, pkce_verifier, state); redirect the user to auth_url.
  2. user logs into Swiggy + consents → Swiggy redirects to our /callback?code=...
  3. exchange_code() → tokens {access_token, refresh_token, expires_in, ...}.
  4. refresh()      → new access_token from a stored refresh_token.

NOTE (unverified): a real token exchange against mcp.swiggy.com has not been
completed end-to-end (requires a human Swiggy login, which can't be automated).
Dynamic client registration is attempted; if the server rejects it, set
SWIGGY_CLIENT_ID to a pre-registered client. Built strictly to the published
metadata — confirm against a live login before relying on it.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import urllib.parse

import httpx

AUTH_ENDPOINT = "https://mcp.swiggy.com/auth/authorize"
TOKEN_ENDPOINT = "https://mcp.swiggy.com/auth/token"
REGISTER_ENDPOINT = "https://mcp.swiggy.com/auth/register"
SCOPES = "mcp:tools mcp:resources mcp:prompts"


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def register_client(redirect_uri: str, client_name: str = "cart-optimizer-web") -> str | None:
    """Dynamic client registration (RFC 7591). Returns a client_id or None on failure."""
    try:
        resp = httpx.post(
            REGISTER_ENDPOINT,
            json={
                "client_name": client_name,
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("client_id")
    except httpx.HTTPError:
        pass
    return None


def resolve_client_id(redirect_uri: str) -> str:
    """Use SWIGGY_CLIENT_ID if set, else attempt dynamic registration, else a
    last-resort default (which the server may reject — see module note)."""
    env = os.getenv("SWIGGY_CLIENT_ID")
    if env:
        return env
    return register_client(redirect_uri) or "cart-optimizer-web"


def start_login(redirect_uri: str, client_id: str) -> tuple[str, str, str]:
    """Return (auth_url, pkce_verifier, state). Caller stashes verifier+state in
    the session and redirects the browser to auth_url."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return auth_url, verifier, state


def exchange_code(code: str, redirect_uri: str, client_id: str, verifier: str) -> dict:
    """Exchange an authorization code for tokens."""
    resp = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def refresh(refresh_token: str, client_id: str) -> dict:
    resp = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()
