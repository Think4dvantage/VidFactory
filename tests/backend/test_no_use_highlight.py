from __future__ import annotations

from vidfactory.core import highlights, shorts, summary
from vidfactory.database.models import Highlight


def _hl(name, start, end, role="normal", type_="video", **kw):
    return Highlight(name=name, start=start, end=end, role=role, type=type_, **kw)


def test_merge_overlaps_skips_no_use():
    segs = highlights.merge_overlaps([_hl("good", 10, 20), _hl("bad", 15, 40, role="no_use")])
    assert [(s["name"], s["start"], s["end"]) for s in segs] == [("good", 10, 20)]


def test_no_use_ranges_only_returns_flagged_video_ranges():
    hls = [_hl("good", 10, 20), _hl("bad", 30, 40, role="no_use"), _hl("launch", 50, 55, role="launch")]
    assert highlights.no_use_ranges(hls) == [(30, 40)]


def test_auto_fill_never_lands_in_excluded_range():
    exclude = [(0.0, 500.0)]  # everything but the last 100 s of a 600 s flight
    segs = summary.auto_fill([], 60.0, 600.0, exclude=exclude)
    assert segs  # the free tail still gets filler
    for s in segs:
        assert s["start"] >= 500.0


def test_auto_fill_without_exclude_is_unchanged():
    segs = summary.auto_fill([], 20.0, 600.0)
    assert sum(s["end"] - s["start"] for s in segs) >= 20.0


def test_subtract_ranges_splits_pool_around_exclusions():
    assert shorts.subtract_ranges([(0.0, 100.0)], [(20.0, 30.0), (60.0, 70.0)]) == [
        (0.0, 20.0), (30.0, 60.0), (70.0, 100.0),
    ]
    assert shorts.subtract_ranges([(0.0, 10.0)], [(0.0, 10.0)]) == []
