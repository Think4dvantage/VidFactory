from __future__ import annotations

import httpx
import pytest

from vidfactory.core import flightlog_client as fl

BASE = "http://fl-test.example"
KEY = "flg_test_key"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_flight_metadata_ok():
    def handler(request):
        assert request.headers["X-API-Key"] == KEY
        assert request.url.path == "/api/integration/v1/flights/abc123"
        return httpx.Response(200, json={
            "id": "abc123", "flight_date": "2026-08-01", "takeoff_time": None,
            "landing_time": None, "launch_site_name": "Amisbühl", "landing_site_name": "Interlaken",
            "category_name": "Thermal", "glider_name": None, "harness_name": None,
            "launch_technique": None, "duration_min": 51, "distance_km": 12.3, "max_alt_m": 2400,
            "alt_gain_m": 470, "site_drop_m": 1500, "total_descent_m": 1600,
            "has_igc_track": True,
            "igc_summary": {"duration_s": 3000, "thermal_count": 4, "best_climb_ms": 3.1,
                             "peak_climb_ms": 4.2, "glide_ratio": 8.5, "distance_km": 12.3,
                             "max_alt_igc_m": 2400, "alt_gain_igc_m": 470},
            "links": [],
        })

    meta = fl.get_flight_metadata(BASE, KEY, "abc123", client=_client(handler))
    assert meta is not None
    assert meta.id == "abc123"
    assert meta.launch_site_name == "Amisbühl"
    assert meta.total_descent_m == 1600
    assert meta.igc_summary.thermal_count == 4


def test_get_flight_metadata_404_returns_none():
    def handler(request):
        return httpx.Response(404, json={"error": {"code": "ENTITY_NOT_FOUND", "message": "Flight not found", "details": {}}})

    assert fl.get_flight_metadata(BASE, KEY, "nope", client=_client(handler)) is None


def test_get_flight_segments_404_returns_empty_list():
    def handler(request):
        return httpx.Response(404, json={"error": {"code": "ENTITY_NOT_FOUND", "message": "no track", "details": {}}})

    assert fl.get_flight_segments(BASE, KEY, "abc123", client=_client(handler)) == []


def test_get_flight_segments_ok():
    def handler(request):
        return httpx.Response(200, json=[
            {"kind": "thermal", "start_offset_s": 842, "start_at": "2026-08-01T09:26:02+00:00",
             "duration_s": 118, "alt_change_m": 210.0, "vertical_velocity_ms": 1.78, "glide_ratio": None},
        ])

    segs = fl.get_flight_segments(BASE, KEY, "abc123", client=_client(handler))
    assert len(segs) == 1
    assert segs[0].kind == "thermal"
    assert segs[0].start_offset_s == 842


@pytest.mark.parametrize("status,code", [(401, "AUTH_REQUIRED"), (403, "PERMISSION_DENIED"), (422, "VALIDATION_FAILED")])
def test_error_envelope_raises_flightlog_error(status, code):
    def handler(request):
        return httpx.Response(status, json={"error": {"code": code, "message": "nope", "details": {}}})

    with pytest.raises(fl.FlightlogError) as exc_info:
        fl.get_flight_metadata(BASE, KEY, "abc123", client=_client(handler))
    assert exc_info.value.status_code == status
    assert exc_info.value.code == code


def test_push_video_link_ok():
    def handler(request):
        assert request.method == "PUT"
        assert request.url.path == "/api/integration/v1/flights/abc123/links/video/42"
        assert request.headers["X-API-Key"] == KEY
        return httpx.Response(200, json={
            "kind": "video", "external_id": "42", "url": "https://youtu.be/xyz",
            "label": "Summary", "created_at": "2026-08-18T10:00:00+00:00", "updated_at": "2026-08-18T10:00:00+00:00",
        })

    link = fl.push_video_link(BASE, KEY, "abc123", 42, "https://youtu.be/xyz", "Summary", client=_client(handler))
    assert link.url == "https://youtu.be/xyz"
    assert link.external_id == "42"
