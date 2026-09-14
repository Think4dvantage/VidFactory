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


def test_resolve_track_credits_uses_tags_and_falls_back_to_filename(monkeypatch):
    def fake_track_meta(runner, file):
        return {"a.mp3": ("Lights Up", "Harris Heller"), "b.mp3": (None, None)}[file]

    monkeypatch.setattr(music, "_track_meta", fake_track_meta)

    credits = music.resolve_track_credits(["a.mp3", "b.mp3"], _StubRunner())

    assert credits == [
        {"file": "a.mp3", "title": "Lights Up", "artist": "Harris Heller"},
        {"file": "b.mp3", "title": "b", "artist": music.DEFAULT_ARTIST},
    ]


def test_resolve_track_credits_splits_streambeats_filename_when_no_tags(monkeypatch):
    # StreamBeats ships files with no embedded tags, named "<N>. <Artist> - <Title>.<ext>" —
    # without this parse, "Harris Heller" ends up jammed into the title as raw filename text
    # (the bug the user actually saw after a real deploy).
    monkeypatch.setattr(music, "_track_meta", lambda runner, file: (None, None))

    credits = music.resolve_track_credits(["/library/EDM/21. Harris Heller - High Tide.mp3"], _StubRunner())

    assert credits == [{
        "file": "/library/EDM/21. Harris Heller - High Tide.mp3",
        "title": "High Tide",
        "artist": "Harris Heller",
    }]


def test_resolve_track_credits_strips_number_and_defaults_artist_for_unlabeled_files(monkeypatch):
    # Most StreamBeats files don't spell the artist out at all, just "<N> <Title>.<ext>" (e.g.
    # "27 B-Roll.mp3") — a real credits box showed this raw, numbered, with no byline at all.
    # The whole library is authored by Harris Heller (see DEFAULT_ARTIST), so that's the credit.
    monkeypatch.setattr(music, "_track_meta", lambda runner, file: (None, None))

    credits = music.resolve_track_credits(["/library/27 B-Roll.mp3", "/library/12 Pastel Blue.mp3"], _StubRunner())

    assert credits == [
        {"file": "/library/27 B-Roll.mp3", "title": "B-Roll", "artist": "Harris Heller"},
        {"file": "/library/12 Pastel Blue.mp3", "title": "Pastel Blue", "artist": "Harris Heller"},
    ]


def test_normalize_music_credits_handles_every_historical_shape():
    assert music.normalize_music_credits(None) == []
    assert music.normalize_music_credits("") == []
    assert music.normalize_music_credits("old/track.mp3") == [
        {"file": "old/track.mp3", "title": "track", "artist": music.DEFAULT_ARTIST}
    ]
    assert music.normalize_music_credits(["a/one.mp3", "b/27 Two.mp3"]) == [
        {"file": "a/one.mp3", "title": "one", "artist": music.DEFAULT_ARTIST},
        {"file": "b/27 Two.mp3", "title": "Two", "artist": music.DEFAULT_ARTIST},
    ]
    assert music.normalize_music_credits("old/21. Harris Heller - High Tide.mp3") == [
        {"file": "old/21. Harris Heller - High Tide.mp3", "title": "High Tide", "artist": "Harris Heller"}
    ]
    # a dict row from before this fix — raw numbered title, no artist — gets repaired in place
    stale = [{"file": "x.mp3", "title": "27 B-Roll", "artist": None}]
    assert music.normalize_music_credits(stale) == [
        {"file": "x.mp3", "title": "B-Roll", "artist": music.DEFAULT_ARTIST}
    ]
    current = [{"file": "x.mp3", "title": "X", "artist": "Someone"}]
    assert music.normalize_music_credits(current) == current


def test_build_credits_text_empty_for_no_credits():
    assert music.build_credits_text([]) == ""


def test_build_credits_text_includes_title_artist_and_license_notice():
    credits = [
        {"file": "a.mp3", "title": "Lights Up", "artist": "Harris Heller"},
        {"file": "b.mp3", "title": "b", "artist": None},
    ]

    text = music.build_credits_text(credits)

    assert '- "Lights Up" by Harris Heller' in text
    assert '- "b"' in text  # no dangling "by" for a track with no artist tag
    assert music.LICENSE_NOTICE in text
    assert "resources" not in text  # no filesystem path leaking into pasted text
    assert "StreamBeats Sync_Use License.pdf" not in text


def test_write_credits_writes_file_only_when_there_are_credits(tmp_path):
    out_path = tmp_path / "Short_01_MusicCredits.txt"

    music.write_credits([], str(out_path))
    assert not out_path.exists()

    music.write_credits([{"file": "a.mp3", "title": "A", "artist": "Artist"}], str(out_path))
    assert out_path.exists()
    assert '"A" by Artist' in out_path.read_text(encoding="utf-8")
