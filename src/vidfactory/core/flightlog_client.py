"""Seam for the future Flightlog service API (not available yet).

Flight-log data (sites, outings, IGC tracks) has moved to a separate Flightlog project/service.
Once it exposes an API, `Project.external_flight_id` will key into it via this function.
"""

from __future__ import annotations


def get_flight_metadata(external_flight_id: int) -> dict | None:
    """Fetch flight metadata (date/site/glider/...) for a Project's external_flight_id.

    Not implemented — the Flightlog service doesn't expose an API yet.
    """
    raise NotImplementedError("Flightlog API integration not yet available")
