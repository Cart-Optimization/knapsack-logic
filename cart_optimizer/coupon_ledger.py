"""Per-branch coupon ledger.

A Swiggy ``restaurantId`` identifies a specific *branch* (the Saki Vihar
McDonald's has a different id from every other McDonald's). Coupons are issued at
the branch level, so a code that worked for one user at a branch will, ~90% of
the time, work again at that same branch. This ledger remembers which codes have
actually produced a discount at each branch, so the next run probes those first
(and rarely misses a good coupon).

Design:
- ``known(restaurant_id)`` → codes that have yielded a real discount here,
  best-first (highest discount seen). These are tried before the generic
  candidate list.
- ``record(restaurant_id, code, discount)`` → log an application result.
  discount > 0 reinforces the code; discount == 0 (failed/no-help) is tracked
  too so persistently-dead codes can be pruned.

The live verifier ALWAYS re-validates against Swiggy, so a stale (expired) code
in the ledger is harmless — applying it just fails and costs one call. ``known``
already drops codes whose recent attempts only ever fail.

``CouponLedger`` is a Protocol so the verifier is testable with an in-memory
fake; ``JsonCouponLedger`` persists to disk for the real app.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

__all__ = [
    "CouponLedger",
    "InMemoryCouponLedger",
    "JsonCouponLedger",
    "PRUNE_AFTER_MISSES",
]

# A code with zero hits and at least this many misses is considered dead and is
# no longer returned by known() (it stays recorded so we don't keep re-adding it).
PRUNE_AFTER_MISSES = 3


class CouponLedger(Protocol):
    def known(self, restaurant_id: str) -> list[str]: ...
    def record(self, restaurant_id: str, code: str, discount: float) -> None: ...


def _rank(entries: dict[str, dict]) -> list[str]:
    """Codes worth trying, best-first: any with a hit (by best_discount, then
    hits), excluding never-worked codes that have missed too many times."""
    alive = [
        (code, e)
        for code, e in entries.items()
        if e.get("hits", 0) > 0 or e.get("misses", 0) < PRUNE_AFTER_MISSES
    ]
    alive.sort(
        key=lambda ce: (ce[1].get("hits", 0) > 0,
                        ce[1].get("best_discount", 0.0),
                        ce[1].get("hits", 0)),
        reverse=True,
    )
    return [code for code, _ in alive]


class InMemoryCouponLedger:
    """Non-persistent ledger (tests, or a single run)."""

    def __init__(self, data: dict[str, dict[str, dict]] | None = None) -> None:
        # {restaurant_id: {code: {"hits": int, "misses": int, "best_discount": float}}}
        self._data: dict[str, dict[str, dict]] = data or {}

    def known(self, restaurant_id: str) -> list[str]:
        return _rank(self._data.get(str(restaurant_id), {}))

    def record(self, restaurant_id: str, code: str, discount: float) -> None:
        if not code:
            return
        branch = self._data.setdefault(str(restaurant_id), {})
        entry = branch.setdefault(code, {"hits": 0, "misses": 0, "best_discount": 0.0})
        if discount and discount > 0:
            entry["hits"] += 1
            entry["best_discount"] = max(entry["best_discount"], float(discount))
        else:
            entry["misses"] += 1

    @property
    def data(self) -> dict[str, dict[str, dict]]:
        return self._data


class JsonCouponLedger(InMemoryCouponLedger):
    """Ledger persisted to a JSON file. Loads on construction, writes on every
    record (small file; simplicity over write-batching). Corrupt/missing file
    starts empty."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        data: dict = {}
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
        super().__init__(data)

    def record(self, restaurant_id: str, code: str, discount: float) -> None:
        super().record(restaurant_id, code, discount)
        self._flush()

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2, sort_keys=True))
