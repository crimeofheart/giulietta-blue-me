"""Evidence levels for every protocol constant in this project.

The Giulietta 940 B-CAN protocol is not publicly documented. Everything we know
comes from one of three places, and they are not equally trustworthy:

  * a capture taken from *this* car             -> CONFIRMED_940
  * reasoning over such a capture               -> INFERRED_940
  * someone else's specific claim about the 940 -> REPORTED_940
  * the same thing seen on two other platforms  -> LIKELY_SHARED
  * a different FCA vehicle's reimplementation  -> OTHER_VEHICLE
  * nothing at all                              -> UNKNOWN

`TxGate` refuses to transmit any frame whose confidence is below the configured
threshold, so the distinction is enforced by the code rather than by comments.
"""

from __future__ import annotations

from enum import IntEnum


class Confidence(IntEnum):
    """Ordered evidence levels. Higher means better evidence."""

    UNKNOWN = 0
    """No evidence at all. Placeholder so the frame can be named before it is known."""

    OTHER_VEHICLE = 1
    """Documented on a different FCA vehicle (Doblo 263, Alfa 159). Reference only.

    The two reference projects disagree about ID width and addressing, so a
    constant at this level is a hypothesis about the Giulietta, never a fact.
    """

    LIKELY_SHARED = 2
    """Independently observed identically on two unrelated FCA platforms.

    Currently only the 6-bit display character map qualifies: fmntf's Doblo map
    and karolkrupa's Alfa 159 map are value-for-value identical.
    """

    REPORTED_940 = 3
    """A specific, credible claim about the Giulietta 940 made by someone else.

    Testimony, not measurement. Someone who has worked on these buses says a
    thing about this model; it is far better than a guess extrapolated from
    another car, and far worse than a capture. Cite the person and the URL.
    """

    INFERRED_940 = 4
    """Derived by reasoning over a capture from this car, but not directly seen.

    Example: seeing the Body Computer retry a PROXI challenge three times tells
    us the retry policy without ever seeing a Blue&Me node answer it.
    """

    CONFIRMED_940 = 5
    """Directly observed in a capture from this car, with the capture cited."""

    @property
    def transmittable_by_default(self) -> bool:
        """Whether the default config would ever put this frame on a real bus."""
        return self >= Confidence.CONFIRMED_940

    @classmethod
    def parse(cls, value: "str | int | Confidence") -> "Confidence":
        """Accept a name, an int, or a Confidence. Used for config parsing."""
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            return cls(value)
        try:
            return cls[str(value).strip().upper()]
        except KeyError:
            raise ValueError(
                f"unknown confidence {value!r}; expected one of "
                + ", ".join(c.name for c in cls)
            ) from None
