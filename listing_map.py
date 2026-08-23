"""
Does a Guesty listing map cleanly onto one sheet property?

A future event-driven caller wants to recompute "just the listing that changed".
That is only sound when the listing owns its rows outright, and two shapes in the
real data break that assumption:

    fans out  one listing produces SEVERAL sheet properties.
              `422 Gravier 201&202` -> `422 Gravier 201`, `422 Gravier 202`
              Recomputing the listing touches rows belonging to more than one
              property, so a narrow query can leave the others inconsistent.

    shared    several listings produce the SAME sheet property.
              `1201 N Roman V2` and `1201 N Roman V3` both -> `1201 N Roman`
              Querying one listing sees only part of what determines that
              property's turnovers, so a turnover flag can be cleared while the
              other listing still justifies it.

This module DETECTS those conditions. It does not resolve them -- the caller is
expected to skip and flag, not guess. Reporting "clean" when a listing is shared
would be worse than refusing to answer, so the comparison is deliberately strict.

Sharing is judged on `processing._canonical_key`, not on the property string,
because the same unit is spelled several ways across channels: `520 E Harris&CH`
and `520 Harris CH` normalise to different-looking properties that are the same
place. String equality would call that pair clean and silently corrupt it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from processing import _canonical_key, normalize_property

# Result kinds. `CLEAN` is the only one a narrow per-listing recompute may act on.
CLEAN = "clean"
FANS_OUT = "fans_out"
SHARED = "shared"
FANS_OUT_AND_SHARED = "fans_out_and_shared"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class ListingResolution:
    """What one listing id maps to, and whether that mapping can be trusted."""

    listing_id: str
    nickname: str
    properties: tuple[str, ...]
    kind: str
    siblings: tuple[str, ...] = field(default=())
    reason: str = ""

    @property
    def is_clean(self) -> bool:
        """True only for exactly one property owned by exactly this listing."""
        return self.kind == CLEAN

    @property
    def sole_property(self) -> str:
        """The single property, when there is one. Empty otherwise."""
        return self.properties[0] if self.kind == CLEAN else ""


def _extract(entry) -> tuple[str, str]:
    """(listing_id, nickname) from a Guesty listing object or a plain pair."""
    if isinstance(entry, (tuple, list)):
        listing_id, nickname = entry[0], entry[1]
    else:
        listing_id = entry.get("_id") or entry.get("id") or entry.get("listingId") or ""
        nickname = entry.get("nickname") or entry.get("title") or entry.get("name") or ""
    return str(listing_id).strip(), str(nickname).strip()


class ListingIndex:
    """Every listing on the account, indexed both ways.

    Build it once per run from whatever listing catalogue the caller already has;
    this module never fetches anything itself.
    """

    def __init__(self, listings=()):
        self._nickname: dict[str, str] = {}
        self._properties: dict[str, tuple[str, ...]] = {}
        self._ids_by_key: dict[str, list[str]] = {}

        for entry in listings:
            listing_id, nickname = _extract(entry)
            if not listing_id or not nickname:
                continue  # nothing to map; the caller's catalogue is incomplete
            props = tuple(normalize_property(nickname))
            self._nickname[listing_id] = nickname
            self._properties[listing_id] = props
            for prop in props:
                ids = self._ids_by_key.setdefault(_canonical_key(prop), [])
                if listing_id not in ids:
                    ids.append(listing_id)

    def __len__(self) -> int:
        return len(self._properties)

    def __contains__(self, listing_id) -> bool:
        return str(listing_id).strip() in self._properties

    def listings_for_property(self, prop: str) -> tuple[str, ...]:
        """Every listing id that produces this property, canonically matched."""
        return tuple(self._ids_by_key.get(_canonical_key(prop), ()))

    def resolve(self, listing_id) -> ListingResolution:
        """Classify one listing. Never raises for an unknown id -- reports it."""
        listing_id = str(listing_id).strip()
        if listing_id not in self._properties:
            return ListingResolution(
                listing_id=listing_id, nickname="", properties=(), kind=UNKNOWN,
                reason=("listing id not in the index -- the catalogue is stale or "
                        "this listing belongs to another account"))

        props = self._properties[listing_id]
        nickname = self._nickname[listing_id]

        siblings: list[str] = []
        for prop in props:
            for other in self._ids_by_key.get(_canonical_key(prop), ()):
                if other != listing_id and other not in siblings:
                    siblings.append(other)

        fans_out = len(props) > 1
        shared = bool(siblings)

        if fans_out and shared:
            kind = FANS_OUT_AND_SHARED
            reason = (f"'{nickname}' splits into {len(props)} properties AND shares "
                      f"at least one with {len(siblings)} other listing(s)")
        elif fans_out:
            kind = FANS_OUT
            reason = (f"'{nickname}' splits into {len(props)} properties: "
                      + ", ".join(props))
        elif shared:
            kind = SHARED
            reason = (f"'{props[0]}' is also produced by {len(siblings)} other "
                      f"listing(s): " + ", ".join(siblings))
        elif not props:
            # normalize_property returned nothing usable.
            kind = UNKNOWN
            reason = f"'{nickname}' did not normalise to any property"
        else:
            kind = CLEAN
            reason = f"'{nickname}' maps to exactly one property: {props[0]}"

        return ListingResolution(listing_id=listing_id, nickname=nickname,
                                 properties=props, kind=kind,
                                 siblings=tuple(sorted(siblings)), reason=reason)

    def combined(self) -> tuple[ListingResolution, ...]:
        """Every listing that is not clean -- the review list for an operator."""
        out = [self.resolve(lid) for lid in sorted(self._properties)]
        return tuple(r for r in out if not r.is_clean)
