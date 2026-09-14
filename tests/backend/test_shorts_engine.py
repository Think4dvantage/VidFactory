from __future__ import annotations

import random

from vidfactory.core import concat, shorts as shorts_engine


def test_pick_clips_spreads_across_equal_buckets():
    random.seed(0)
    pool = [(0.0, 3000.0)]
    used: shorts_engine.UsedMap = {}

    clips = shorts_engine.pick_clips(pool, 3, 3.0, used, "full")

    assert len(clips) == 3
    # one clip per third of the pool, chronologically sorted
    assert 0.0 <= clips[0][0] < 1000.0
    assert 1000.0 <= clips[1][0] < 2000.0
    assert 2000.0 <= clips[2][0] < 3000.0


def test_pick_clips_over_many_shorts_does_not_cluster():
    random.seed(1)
    pool = [(0.0, 3000.0)]
    used: shorts_engine.UsedMap = {}

    all_clips = []
    for _ in range(12):
        all_clips.extend(shorts_engine.pick_clips(pool, 3, 3.0, used, "full"))

    starts = sorted(c[0] for c in all_clips)
    # with even bucketed sampling, the spread should span most of the pool, not collapse
    # into a small region the way naive per-window random.choice used to (see M15).
    assert starts[-1] - starts[0] > 2000.0


def test_pick_clips_respects_used_map_no_overlap():
    random.seed(2)
    pool = [(0.0, 30.0)]
    used: shorts_engine.UsedMap = {}

    first = shorts_engine.pick_clips(pool, 3, 3.0, used, "full")
    second = shorts_engine.pick_clips(pool, 3, 3.0, used, "full")

    for a in first:
        for b in second:
            assert a[1] <= b[0] or b[1] <= a[0]  # no overlap


def test_pick_clips_empty_pool_returns_empty():
    assert shorts_engine.pick_clips([], 3, 3.0, {}, "full") == []


def test_hike_output_duration_no_speedup():
    class FakeRunner:
        def get_video_info(self, path):
            return (1920, 1080, {"a.mp4": 60.0, "b.mp4": 40.0}[path])

    dur = concat.hike_output_duration(["a.mp4", "b.mp4"], 1.0, FakeRunner())
    assert dur == 100.0


def test_hike_output_duration_applies_speed_factor():
    class FakeRunner:
        def get_video_info(self, path):
            return (1920, 1080, 320.0)

    dur = concat.hike_output_duration(["a.mp4"], 32.0, FakeRunner())
    assert dur == 10.0


def test_concatenate_single_part_no_hike_copies_instead_of_encoding(tmp_path):
    class FakeRunner:
        def get_video_info(self, path):
            return (1920, 1080, 42.0)

    class ExplodingGPU:
        def encoding_args(self, bitrate):
            raise AssertionError("a single source part must not go through the encoder")

    src = tmp_path / "part1.mp4"
    src.write_bytes(b"fake video data")
    output = tmp_path / "out" / "full_flight.mp4"
    stages: list[str] = []
    progress: list[tuple[float, str]] = []

    result = concat.concatenate(
        [str(src)], str(output), FakeRunner(), ExplodingGPU(),
        progress_cb=lambda frac, speed: progress.append((frac, speed)),
        stage_cb=stages.append,
    )

    assert output.read_bytes() == b"fake video data"
    assert result == {"output": str(output), "duration": 42.0}
    assert stages == ["Copying"]
    assert progress == [(1.0, "")]
