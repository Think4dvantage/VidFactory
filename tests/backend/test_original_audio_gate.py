from __future__ import annotations

from types import SimpleNamespace

from vidfactory.config import MusicSection
from vidfactory.core import music


def _cfg(**kw):
    return SimpleNamespace(music=MusicSection(**kw))


def test_defaults_let_music_lead_and_gate_original(monkeypatch):
    m = MusicSection()
    assert m.music_volume > m.original_audio_volume
    monkeypatch.setattr(music, "get_config", lambda: _cfg())
    f = music.original_audio_filter(0.4)
    assert f.startswith("agate=threshold=0.08:ratio=3:range=0.25")
    assert f.endswith("volume=0.4")


def test_gate_can_be_disabled(monkeypatch):
    monkeypatch.setattr(music, "get_config", lambda: _cfg(original_audio_gate=False))
    assert music.original_audio_filter(0.4) == "volume=0.4"
