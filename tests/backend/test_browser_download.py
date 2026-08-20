from __future__ import annotations

from pathlib import Path

from vidfactory.core import filebrowser


class _FakeConfig:
    def __init__(self, root: Path):
        self._root = root

    def mount_roots(self) -> dict[str, Path]:
        return {"music": self._root}

    def writable_roots(self) -> set[str]:
        return set()


def test_download_returns_file(auth_client, tmp_path, monkeypatch):
    (tmp_path / "track.mp3").write_bytes(b"fake-audio-bytes")
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path))

    resp = auth_client.get("/api/download/music", params={"path": "track.mp3"})

    assert resp.status_code == 200
    assert resp.content == b"fake-audio-bytes"
    assert "track.mp3" in resp.headers["content-disposition"]


def test_download_handles_ampersand_in_filename(auth_client, tmp_path, monkeypatch):
    (tmp_path / "Rock & Roll.mp3").write_bytes(b"fake-audio-bytes")
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path))

    resp = auth_client.get("/api/download/music", params={"path": "Rock & Roll.mp3"})

    assert resp.status_code == 200
    assert resp.content == b"fake-audio-bytes"


def test_listing_urlencodes_ampersand_in_download_link(auth_client, tmp_path, monkeypatch):
    (tmp_path / "Rock & Roll.mp3").write_bytes(b"fake-audio-bytes")
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path))

    resp = auth_client.get("/api/browse/music", params={"path": ""})

    assert resp.status_code == 200
    assert "path=Rock+%26+Roll.mp3" in resp.text or "path=Rock%20%26%20Roll.mp3" in resp.text
    assert "path=Rock & Roll.mp3" not in resp.text


def test_download_missing_file_404s(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path))

    resp = auth_client.get("/api/download/music", params={"path": "nope.mp3"})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ENTITY_NOT_FOUND"


def test_download_rejects_path_traversal(auth_client, tmp_path, monkeypatch):
    monkeypatch.setattr(filebrowser, "get_config", lambda: _FakeConfig(tmp_path))

    resp = auth_client.get("/api/download/music", params={"path": "../../etc/passwd"})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PATH_NOT_ALLOWED"
