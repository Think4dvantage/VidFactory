"""Client for Flightlog's frozen `/api/integration/v1` contract.

Every function takes `base_url`/`api_key` explicitly rather than reading `config.py` itself — the
key is per-user (see `database.models.User.flightlog_api_key`), so callers own picking the right
one. An optional `client` (an `httpx.Client`) can be passed in for tests to inject
`httpx.MockTransport` — omitted, each call opens a short-lived client of its own.
"""

from __future__ import annotations

import logging

import httpx

from vidfactory.models.flightlog import FlightLink, FlightMetadataOut, SegmentOut

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


class FlightlogError(Exception):
    """Raised for any non-2xx response, parsed from Flightlog's `{"error": {...}}` envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"Flightlog {status_code} {code}: {message}")


def _headers(api_key: str) -> dict:
    return {"X-API-Key": api_key}


def _raise_for_error(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        err = resp.json()["error"]
        code, message = err["code"], err["message"]
    except (ValueError, KeyError, TypeError):
        code, message = "UNKNOWN", resp.text[:200]
    raise FlightlogError(resp.status_code, code, message)


def _request(client: httpx.Client | None, method: str, url: str, api_key: str, **kwargs) -> httpx.Response:
    if client is not None:
        return client.request(method, url, headers=_headers(api_key), **kwargs)
    with httpx.Client(timeout=_TIMEOUT) as c:
        return c.request(method, url, headers=_headers(api_key), **kwargs)


def get_flight_metadata(
    base_url: str, api_key: str, flight_id: str, client: httpx.Client | None = None
) -> FlightMetadataOut | None:
    """None if the flight doesn't exist (or isn't owned by this key's pilot) — both 404."""
    url = f"{base_url}/api/integration/v1/flights/{flight_id}"
    resp = _request(client, "GET", url, api_key)
    if resp.status_code == 404:
        return None
    _raise_for_error(resp)
    return FlightMetadataOut.model_validate(resp.json())


def get_flight_segments(
    base_url: str, api_key: str, flight_id: str, client: httpx.Client | None = None
) -> list[SegmentOut]:
    """Empty list if the flight has no IGC track (404) rather than raising."""
    url = f"{base_url}/api/integration/v1/flights/{flight_id}/segments"
    resp = _request(client, "GET", url, api_key)
    if resp.status_code == 404:
        return []
    _raise_for_error(resp)
    return [SegmentOut.model_validate(s) for s in resp.json()]


def push_video_link(
    base_url: str,
    api_key: str,
    flight_id: str,
    project_id: int,
    url_: str,
    label: str | None = None,
    client: httpx.Client | None = None,
) -> FlightLink:
    endpoint = f"{base_url}/api/integration/v1/flights/{flight_id}/links/video/{project_id}"
    body = {"url": url_}
    if label:
        body["label"] = label
    resp = _request(client, "PUT", endpoint, api_key, json=body)
    _raise_for_error(resp)
    return FlightLink.model_validate(resp.json())
