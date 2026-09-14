from __future__ import annotations

from vidfactory.core import summary


class FakeGpu:
    def encoding_args(self, bitrate):
        return ["-c:v", "libx264", "-b:v", bitrate]


class FakeRunner:
    """Records every encode() call (args + total) and answers get_video_info/probe with
    canned values -- no real ffmpeg/ffprobe involved (matches this repo's local-only
    verification: no FFmpeg locally, see .ai/instructions/06-testing-conventions.md)."""

    def __init__(self, durations: dict[str, float]):
        self.durations = durations
        self.calls: list[tuple[list[str], float]] = []

    def get_video_info(self, path):
        return (1920, 1080, self.durations.get(path, 0.0))

    def probe(self, path):
        return {"streams": [{"r_frame_rate": "30/1"}]}

    def encode(self, args, total, progress_cb, cancel_event):
        self.calls.append((args, total))


CTA_KWARGS = dict(cta_image="/cta.jpg", cta_duration=3.0, cta_line1="for more relaxed Paragliding",
                   cta_line2="Like & Subscribe")


def test_build_summary_appends_cta_segment_to_the_concat():
    runner = FakeRunner({"/out.mp4": 33.0})
    seg = {"name": "Thermal", "comment": None, "start": 10.0, "end": 20.0, "role": "normal", "type": "video"}

    res = summary.build_summary(
        "/full.mp4", [seg], "/out.mp4", runner, FakeGpu(),
        width=1920, height=1080, fps="30", **CTA_KWARGS,
    )

    assert len(runner.calls) == 1
    args, total = runner.calls[0]
    fc = args[args.index("-filter_complex") + 1]

    assert total == 10.0 + 3.0  # segment duration + CTA
    assert "concat=n=2:v=1:a=1" in fc  # 1 real segment + 1 CTA segment
    assert "/cta.jpg" in args  # CTA image looped as an input
    assert "for more relaxed Paragliding" not in fc  # text goes via textfile=, not inlined
    assert res["duration"] == 33.0


def test_build_summary_cta_survives_music_mix():
    runner = FakeRunner({"/out.mp4": 40.0})
    seg = {"name": "Thermal", "comment": None, "start": 0.0, "end": 30.0, "role": "normal", "type": "video"}

    summary.build_summary(
        "/full.mp4", [seg], "/out.mp4", runner, FakeGpu(),
        width=1920, height=1080, fps="30", music_bed="/bed.mp3", **CTA_KWARGS,
    )

    args, _ = runner.calls[0]
    fc = args[args.index("-filter_complex") + 1]
    # the music mix operates on [outa], which is only produced by the concat *after* the CTA
    # segment is folded in -- so music keeps playing under the CTA, matching shorts.build_short.
    assert "concat=n=2:v=1:a=1[outv][outa]" in fc
    assert fc.index("concat=n=2") < fc.index("amix")


def test_build_fullflight_with_music_appends_a_separately_encoded_cta_via_concat_demuxer():
    runner = FakeRunner({"/full.mp4": 300.0, "/out.mp4": 303.0})

    res = summary.build_fullflight_with_music(
        "/full.mp4", "/out.mp4", "/bed.mp3", runner, FakeGpu(),
        **CTA_KWARGS,
    )

    assert len(runner.calls) == 3
    (mix_args, mix_total), (cta_args, cta_total), (concat_args, concat_total) = runner.calls

    assert mix_total == 300.0
    assert "-c:v" in mix_args and mix_args[mix_args.index("-c:v") + 1] == "copy"  # main video not re-encoded

    assert cta_total == 3.0
    assert "/cta.jpg" in cta_args
    assert "-c:v" in cta_args and cta_args[cta_args.index("-c:v") + 1] == "libx264"  # CTA is re-encoded

    assert concat_total == 300.0 + 3.0
    assert concat_args[:5] == ["-f", "concat", "-safe", "0", "-i"]
    assert concat_args[-4:-2] == ["-c", "copy"]  # join is a stream copy, no second full re-encode

    assert res["duration"] == 303.0
