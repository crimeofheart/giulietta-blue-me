"""Profile lookup by key."""

from __future__ import annotations

from .frame import Profile
from .profiles import alfa939, doblo263, giulietta940

PROFILES: dict[str, Profile] = {
    p.key: p for p in (giulietta940.PROFILE, doblo263.PROFILE, alfa939.PROFILE)
}

DEFAULT_PROFILE = giulietta940.PROFILE.key

#: Profiles describing a vehicle that is not ours. Loading one is fine -- it is
#: how captures get compared -- but TxGate additionally refuses to transmit from
#: a profile whose key is not the configured vehicle profile.
REFERENCE_ONLY = frozenset({doblo263.PROFILE.key, alfa939.PROFILE.key})


def get(key: str) -> Profile:
    try:
        return PROFILES[key]
    except KeyError:
        raise KeyError(
            f"unknown profile {key!r}; available: {', '.join(sorted(PROFILES))}"
        ) from None
