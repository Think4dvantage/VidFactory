from __future__ import annotations

from pathlib import Path

from vidfactory.core import filebrowser


class _FakeConfig:
    def __init__(self, root: Path, writable: set[str]):
        self._root = root
        self._writable = writable

    def mount_roots(self) -> dict[str, Path]:
        return {"music": self._root, "output_shorts": self._root}

    def writable_roots(self) -> set[str]:
        return self._writable


def test_delete_removes_file_under_writable_root(tmp_path, monkeypatch):
    target = tmp_path / "short.mp4"
    target.write_bytes(b"fake-video-bytes")
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path, {"output_shorts"}))

    filebrowser.delete("output_shorts", "short.mp4")

    assert not target.exists()


def test_delete_rejects_non_writable_root(tmp_path, monkeypatch):
    target = tmp_path / "track.mp3"
    target.write_bytes(b"fake-audio-bytes")
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path, set()))

    try:
        filebrowser.delete("music", "track.mp3")
        assert False, "expected PathNotAllowed"
    except filebrowser.PathNotAllowed:
        pass
    assert target.exists()


def test_delete_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path, {"output_shorts"}))

    try:
        filebrowser.delete("output_shorts", "../../etc/passwd")
        assert False, "expected PathNotAllowed"
    except filebrowser.PathNotAllowed:
        pass


def test_delete_rejects_directory(tmp_path, monkeypatch):
    (tmp_path / "subdir").mkdir()
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path, {"output_shorts"}))

    try:
        filebrowser.delete("output_shorts", "subdir")
        assert False, "expected PathNotAllowed"
    except filebrowser.PathNotAllowed:
        pass
    assert (tmp_path / "subdir").exists()


def test_delete_endpoint_removes_selected_files(auth_client, tmp_path, monkeypatch):
    (tmp_path / "a.mp4").write_bytes(b"a")
    (tmp_path / "b.mp4").write_bytes(b"b")
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path, {"output_shorts"}))

    resp = auth_client.post(
        "/api/browse/output_shorts/delete",
        data={"paths": ["a.mp4", "b.mp4"], "path": ""},
    )

    assert resp.status_code == 200
    assert not (tmp_path / "a.mp4").exists()
    assert not (tmp_path / "b.mp4").exists()


def test_delete_endpoint_on_readonly_root_reports_error_and_keeps_file(auth_client, tmp_path, monkeypatch):
    (tmp_path / "track.mp3").write_bytes(b"fake-audio-bytes")
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path, set()))

    resp = auth_client.post(
        "/api/browse/music/delete",
        data={"paths": ["track.mp3"], "path": ""},
    )

    assert resp.status_code == 200
    assert (tmp_path / "track.mp3").exists()
    assert "track.mp3" in resp.text
