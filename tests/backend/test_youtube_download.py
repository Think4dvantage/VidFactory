from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from vidfactory.api.routers import projects as projects_router
from vidfactory.core import youtube_download
from vidfactory.core.jobs import registry
from vidfactory.database.models import Project, SourcePart
from vidfactory.models.flightlog import FlightLink, FlightMetadataOut

YT = "https://www.youtube.com/watch?v=abc"


def _link(label, url=YT):
    return FlightLink(kind="video", external_id="1", url=url, label=label)


@pytest.mark.parametrize("label", ["Full Flight", "FullFlight", "full-flight", "My FULL  flight 4K"])
def test_label_variants_match(label):
    assert youtube_download.find_fullflight_url([_link(label)]) == YT


def test_no_match_cases():
    assert youtube_download.find_fullflight_url([]) is None
    assert youtube_download.find_fullflight_url([_link("Summary")]) is None
    assert youtube_download.find_fullflight_url([_link(None)]) is None
    # right label, but not a YouTube URL -> never handed to yt-dlp
    assert youtube_download.find_fullflight_url([_link("Full Flight", "https://evil.example/x")]) is None
    assert youtube_download.find_fullflight_url([_link("Full Flight", "https://notyoutube.com/x")]) is None


def test_first_matching_link_wins():
    links = [_link("Summary", "https://youtu.be/s"), _link("Full Flight", "https://youtu.be/f")]
    assert youtube_download.find_fullflight_url(links) == "https://youtu.be/f"


class _Cfg:
    flightlog = SimpleNamespace(base_url="http://fl-test.example")


def test_resolve_from_flightlog(db_session, user, monkeypatch):
    user.flightlog_api_key = "k"
    project = Project(owner_id=user.id, flight_type="normal_flight", external_flight_id="f1")
    db_session.add(project)
    db_session.commit()
    monkeypatch.setattr(youtube_download, "get_config", lambda: _Cfg())
    monkeypatch.setattr(
        youtube_download.flightlog_client, "get_flight_metadata",
        lambda *a, **k: FlightMetadataOut(id="f1", links=[_link("Full Flight")]),
    )
    assert youtube_download.resolve_fullflight_url(project) == YT


def test_resolve_is_best_effort(db_session, user, monkeypatch):
    project = Project(owner_id=user.id, flight_type="normal_flight", external_flight_id="f1")
    db_session.add(project)
    db_session.commit()
    monkeypatch.setattr(youtube_download, "get_config", lambda: _Cfg())
    # no API key -> None without calling Flightlog
    assert youtube_download.resolve_fullflight_url(project) is None

    user.flightlog_api_key = "k"

    def boom(*a, **k):
        raise youtube_download.flightlog_client.FlightlogError(500, "X", "down")

    monkeypatch.setattr(youtube_download.flightlog_client, "get_flight_metadata", boom)
    assert youtube_download.resolve_fullflight_url(project) is None


def _project(db_session, user, **kw):
    p = Project(owner_id=user.id, flight_type="normal_flight", **kw)
    db_session.add(p)
    db_session.commit()
    return p


def test_set_flightlog_id_offers_download(auth_client, db_session, user, monkeypatch):
    p = _project(db_session, user)
    monkeypatch.setattr(youtube_download, "resolve_fullflight_url", lambda project: YT)
    r = auth_client.post(f"/api/projects/{p.id}/flightlog-id", data={"external_flight_id": "f1"})
    assert r.status_code == 204
    assert r.headers["HX-Trigger"] == "vf-youtube-offer"
    assert "HX-Redirect" not in r.headers
    assert YT not in r.text


def test_set_flightlog_id_redirects_when_nothing_to_offer(auth_client, db_session, user, monkeypatch, tmp_path):
    p = _project(db_session, user)
    monkeypatch.setattr(youtube_download, "resolve_fullflight_url", lambda project: None)
    r = auth_client.post(f"/api/projects/{p.id}/flightlog-id", data={"external_flight_id": "f1"})
    assert r.headers["HX-Redirect"] == f"/projects/{p.id}"

    # existing footage -> no lookup, no offer
    ff = tmp_path / "ff.mp4"
    ff.write_bytes(b"x")
    q = _project(db_session, user, full_flight_file=str(ff))
    monkeypatch.setattr(youtube_download, "resolve_fullflight_url", lambda project: YT)
    r = auth_client.post(f"/api/projects/{q.id}/flightlog-id", data={"external_flight_id": "f1"})
    assert "HX-Trigger" not in r.headers


def test_download_endpoint_queues_job(auth_client, db_session, user, monkeypatch):
    p = _project(db_session, user)
    monkeypatch.setattr(youtube_download, "resolve_fullflight_url", lambda project: YT)
    seen = {}

    def fake_run(kind, owner_id, target, project_id=0, title=""):
        seen.update(kind=kind, project_id=project_id)
        return SimpleNamespace(id="job1")

    monkeypatch.setattr(projects_router.registry, "run", fake_run)
    r = auth_client.post(f"/api/projects/{p.id}/fullflight/download-youtube")
    assert r.json() == {"job_id": "job1"}
    assert seen == {"kind": "ytdownload", "project_id": p.id}


def test_download_endpoint_refusals(auth_client, db_session, user, monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_download, "resolve_fullflight_url", lambda project: None)
    p = _project(db_session, user)
    assert auth_client.post(f"/api/projects/{p.id}/fullflight/download-youtube").status_code == 404

    ff = tmp_path / "ff.mp4"
    ff.write_bytes(b"x")
    has_ff = _project(db_session, user, full_flight_file=str(ff))
    r = auth_client.post(f"/api/projects/{has_ff.id}/fullflight/download-youtube")
    assert r.status_code == 409 and r.json()["error"]["code"] == "HAS_FOOTAGE"

    has_part = _project(db_session, user)
    db_session.add(SourcePart(project_id=has_part.id, file="a.mp4", order=0))
    db_session.commit()
    r = auth_client.post(f"/api/projects/{has_part.id}/fullflight/download-youtube")
    assert r.status_code == 409

    assert auth_client.post("/api/projects/9999/fullflight/download-youtube").status_code == 404


def test_download_endpoint_conflicts_with_active_job(auth_client, db_session, user, monkeypatch):
    p = _project(db_session, user)
    monkeypatch.setattr(youtube_download, "resolve_fullflight_url", lambda project: YT)
    release = threading.Event()
    registry.run("ytdownload", user.id, lambda job: release.wait(timeout=2), project_id=p.id)
    r = auth_client.post(f"/api/projects/{p.id}/fullflight/download-youtube")
    release.set()
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFLICT"


@pytest.fixture(autouse=True)
def _ffmpeg_on_path(monkeypatch):
    monkeypatch.setattr(youtube_download.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_download_fails_clearly_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube_download.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", SimpleNamespace())
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        youtube_download.download(YT, str(tmp_path / "o.mp4"), ffmpeg_path="ffmpeg")


def test_download_passes_best_quality_options(tmp_path, monkeypatch):
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            captured["urls"] = urls
            (tmp_path / "out.mp4").write_bytes(b"x")

    fake = SimpleNamespace(YoutubeDL=FakeYDL, utils=SimpleNamespace(DownloadError=RuntimeError))
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake)
    res = youtube_download.download(YT, str(tmp_path / "out.mp4"), ffmpeg_path="ffmpeg")
    assert res["output"] == str(tmp_path / "out.mp4")
    assert captured["urls"] == [YT]
    assert captured["opts"]["ffmpeg_location"] == "/usr/bin/ffmpeg"  # resolved, not the bare name
    o = captured["opts"]
    assert o["overwrites"] is False  # keep finished streams from an earlier attempt
    assert o["retries"] == 10 and o["fragment_retries"] == 10 and o["extractor_retries"] == 3
    assert o["http_chunk_size"] == 10 * 1024 * 1024
    assert captured["opts"]["format"] == "bv*+ba/b"
    assert captured["opts"]["merge_output_format"] == "mp4"
    assert captured["opts"]["outtmpl"] == str(tmp_path / "out") + ".%(ext)s"


def test_download_cancel_via_hook(tmp_path, monkeypatch):
    cancel = threading.Event()

    class FakeYDL:
        def __init__(self, opts):
            self.hook = opts["progress_hooks"][0]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            cancel.set()
            self.hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})

    fake = SimpleNamespace(YoutubeDL=FakeYDL, utils=SimpleNamespace(DownloadError=RuntimeError))
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake)
    with pytest.raises(youtube_download.DownloadCancelled):
        youtube_download.download(YT, str(tmp_path / "o.mp4"), cancel_event=cancel)


class _Err(Exception):
    pass


def _flaky_ydl(tmp_path, errors):
    """A fake yt_dlp whose download() raises each of `errors` in turn, then succeeds."""
    calls = {"n": 0}

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            calls["n"] += 1
            if errors:
                raise _Err(errors.pop(0))
            (tmp_path / "o.mp4").write_bytes(b"x")

    return SimpleNamespace(YoutubeDL=FakeYDL, utils=SimpleNamespace(DownloadError=_Err)), calls


def test_retries_transient_http_error_then_succeeds(tmp_path, monkeypatch):
    fake, calls = _flaky_ydl(tmp_path, ["unable to download video data: HTTP Error 403: Forbidden"])
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake)
    monkeypatch.setattr(youtube_download, "_BACKOFF_S", 0)
    stages = []
    res = youtube_download.download(YT, str(tmp_path / "o.mp4"), stage_cb=stages.append)
    assert res["output"] == str(tmp_path / "o.mp4")
    assert calls["n"] == 2
    assert "Retrying download (2/4)" in stages


def test_gives_up_after_max_attempts(tmp_path, monkeypatch):
    fake, calls = _flaky_ydl(tmp_path, ["HTTP Error 403: Forbidden"] * 10)
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake)
    monkeypatch.setattr(youtube_download, "_BACKOFF_S", 0)
    with pytest.raises(_Err):
        youtube_download.download(YT, str(tmp_path / "o.mp4"))
    assert calls["n"] == youtube_download._ATTEMPTS


def test_non_http_error_is_not_retried(tmp_path, monkeypatch):
    fake, calls = _flaky_ydl(tmp_path, ["Private video. Sign in if you've been granted access"])
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake)
    monkeypatch.setattr(youtube_download, "_BACKOFF_S", 0)
    with pytest.raises(_Err):
        youtube_download.download(YT, str(tmp_path / "o.mp4"))
    assert calls["n"] == 1


def test_cancel_during_retry_wait(tmp_path, monkeypatch):
    fake, calls = _flaky_ydl(tmp_path, ["HTTP Error 403: Forbidden"] * 10)
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake)
    monkeypatch.setattr(youtube_download, "_BACKOFF_S", 0.01)
    cancel = threading.Event()
    cancel.set()  # cancelled before/while backing off
    with pytest.raises(youtube_download.DownloadCancelled):
        youtube_download.download(YT, str(tmp_path / "o.mp4"), cancel_event=cancel)
    assert calls["n"] == 1


def test_missing_full_flight_file_does_not_block_redownload(auth_client, db_session, user, monkeypatch):
    # full_flight_file is set but the file was deleted (e.g. via the file browser)
    p = _project(db_session, user, full_flight_file="/nonexistent/ff.mp4", external_flight_id="f1")
    monkeypatch.setattr(youtube_download, "resolve_fullflight_url", lambda project: YT)
    monkeypatch.setattr(projects_router.registry, "run", lambda *a, **k: SimpleNamespace(id="j"))
    assert auth_client.post(f"/api/projects/{p.id}/fullflight/download-youtube").json() == {"job_id": "j"}
    assert 'id="yt-download-btn"' in auth_client.get(f"/projects/{p.id}").text


def test_download_button_only_with_flight_id_and_no_footage(auth_client, db_session, user, tmp_path):
    no_id = _project(db_session, user)
    assert 'id="yt-download-btn"' not in auth_client.get(f"/projects/{no_id.id}").text
    ff = tmp_path / "ff.mp4"
    ff.write_bytes(b"x")
    has_ff = _project(db_session, user, external_flight_id="f1", full_flight_file=str(ff))
    assert 'id="yt-download-btn"' not in auth_client.get(f"/projects/{has_ff.id}").text
    ok = _project(db_session, user, external_flight_id="f1")
    assert 'id="yt-download-btn"' in auth_client.get(f"/projects/{ok.id}").text


def test_merge_postprocessor_hook_sets_stage(tmp_path, monkeypatch):
    class FakeYDL:
        def __init__(self, opts):
            self.pp = opts["postprocessor_hooks"][0]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            self.pp({"postprocessor": "FFmpegMetadata", "status": "started"})  # ignored
            self.pp({"postprocessor": "Merger", "status": "started"})
            self.pp({"postprocessor": "Merger", "status": "finished"})
            (tmp_path / "o.mp4").write_bytes(b"x")

    fake = SimpleNamespace(YoutubeDL=FakeYDL, utils=SimpleNamespace(DownloadError=RuntimeError))
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", fake)
    stages = []
    youtube_download.download(YT, str(tmp_path / "o.mp4"), stage_cb=stages.append)
    assert stages == ["Downloading from YouTube", "Merging video+audio (ffmpeg)"]


@pytest.mark.parametrize("codec,status", [("vp9", 400), ("av1", 400), ("h264", 200)])
def test_fullmusic_refuses_vp9_av1(auth_client, db_session, user, monkeypatch, codec, status):
    p = _project(db_session, user, full_flight_file="ff.mp4")
    monkeypatch.setattr(projects_router, "_video_codec", lambda path: codec)
    monkeypatch.setattr(projects_router.registry, "run", lambda *a, **k: SimpleNamespace(id="j"))
    r = auth_client.post(f"/api/projects/{p.id}/fullmusic/build", data={"music_path": "/m"})
    assert r.status_code == status
    if status == 400:
        assert r.json()["error"]["code"] == "UNSUPPORTED_CODEC"
