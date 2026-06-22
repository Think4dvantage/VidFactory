"""IGC track analysis — derive per-flight climb/thermal/glide aggregates via libigc.

The key statistic the Flugbuch can't give: *cumulative* altitude climbed across every thermal
(you climb, glide down, climb again), not just max-altitude minus launch elevation.
"""

from __future__ import annotations

import datetime
import logging

import libigc

logger = logging.getLogger(__name__)


class IgcError(Exception):
    """Raised when an IGC file can't be parsed or fails validity checks."""


def _ts(fix) -> datetime.datetime | None:
    if fix is None:
        return None
    return datetime.datetime.utcfromtimestamp(fix.timestamp)


def analyze(path: str) -> dict:
    """Parse an IGC file and return aggregate flight stats. Raises IgcError on bad input."""
    try:
        flight = libigc.Flight.create_from_file(str(path))
    except Exception as exc:  # libigc raises bare exceptions on malformed files
        raise IgcError(f"could not read IGC file: {exc}") from exc
    if not flight.valid:
        raise IgcError("; ".join(flight.notes) if flight.notes else "invalid IGC file")

    glides = flight.glides

    # libigc flags *any* circling as a "thermal" — including descending spirals and wingovers,
    # which are common in paragliding. A thermal is circling that actually gains height, so we
    # count only climbing circles. This keeps best/avg climb positive and the count meaningful.
    thermals = [t for t in flight.thermals if t.alt_change() > 0]
    total_climb = sum(t.alt_change() for t in thermals)
    thermal_seconds = sum(t.time_change() for t in thermals)
    climb_rates = [t.vertical_velocity() for t in thermals if t.time_change() > 0]

    # Altitude: prefer barometric (press_alt); fall back to GNSS when no baro is recorded.
    press = [f.press_alt for f in flight.fixes if f.press_alt]
    gnss = [f.gnss_alt for f in flight.fixes if f.gnss_alt]
    alts = press if press else gnss
    max_alt = max(alts) if alts else None

    # Achieved over-ground glide = total glide distance / total altitude lost on glides.
    # Robust (one shallow segment can't inflate it) and honest: it includes air-mass lift,
    # so it's the glide you actually got over the ground, not the wing's still-air L/D.
    descending = [g for g in glides if g.alt_change() < 0]
    glide_drop_m = sum(-g.alt_change() for g in descending)
    glide_dist_m = sum(g.track_length for g in descending) * 1000
    glide_ratio = round(glide_dist_m / glide_drop_m, 1) if glide_drop_m else None
    total_glide_km = sum(g.track_length for g in glides) if glides else None

    takeoff, landing = _ts(flight.takeoff_fix), _ts(flight.landing_fix)
    duration_s = int((landing - takeoff).total_seconds()) if (takeoff and landing) else None

    return {
        "takeoff_at": takeoff,
        "landing_at": landing,
        "duration_s": duration_s,
        "max_alt_m": round(max_alt) if max_alt is not None else None,
        "thermal_count": len(thermals),
        "total_climb_m": round(total_climb),
        "best_climb_ms": round(max(climb_rates), 2) if climb_rates else None,
        "avg_climb_ms": round(total_climb / thermal_seconds, 2) if thermal_seconds else None,
        "glide_count": len(glides),
        "total_glide_km": round(total_glide_km, 1) if total_glide_km else None,
        "glide_ratio": glide_ratio,
    }
