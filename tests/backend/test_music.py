from __future__ import annotations

from vidfactory.core import music


class _StubRunner:
    def get_video_info(self, file_path: str) -> tuple[int, int, float]:
        return (0, 0, 5.0)


def test_scan_durations_recurses_into_subfolders(tmp_path):
    (tmp_path / "1. Lights").mkdir()
    (tmp_path / "1. Lights" / "track.wav").write_bytes(b"fake")
    (tmp_path / "2. Stage").mkdir()
    (tmp_path / "2. Stage" / "other.wav").write_bytes(b"fake")

    durations = music._scan_durations(tmp_path, _StubRunner(), tmp_path / "cache")

    assert len(durations) == 2
    assert all(v == 5.0 for v in durations.values())


def test_select_tracks_none_for_folder_with_no_nested_audio(tmp_path):
    (tmp_path / "empty_subfolder").mkdir()

    sel = music.select_tracks(str(tmp_path), 30.0, _StubRunner(), tmp_path / "cache")

    assert sel.mode == "none"
    assert sel.tracks == []


def test_scan_durations_logs_on_cache_hit(tmp_path, caplog):
    (tmp_path / "1. Lights").mkdir()
    (tmp_path / "1. Lights" / "track.wav").write_bytes(b"fake")
    cache_dir = tmp_path / "cache"

    music._scan_durations(tmp_path, _StubRunner(), cache_dir)  # populates the cache
    caplog.clear()
    with caplog.at_level("INFO", logger="vidfactory.core.music"):
        durations = music._scan_durations(tmp_path, _StubRunner(), cache_dir)  # cache hit

    assert len(durations) == 1
    assert any("Scanned" in r.message for r in caplog.records)
