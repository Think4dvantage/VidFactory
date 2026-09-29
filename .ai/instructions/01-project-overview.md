# Project Overview — VidFactory

> Rewritten 2026-09-29 (M28) — the original M0a blueprint (Outing/`Flugbuch.xlsx`/NAS workflow)
> was removed. Per-milestone detail lives in `context/features.md`, schema/pipeline/routes in
> `context/architecture.md`; this file is the stable orientation.

## What This Is

VidFactory is a self-hosted web app for a paragliding YouTube creator's **post-flight video
pipeline**. A **Project** (one flight, owned by one logged-in user) gets a full-flight video — from
uploaded source footage, or downloaded from YouTube when the flight's Flightlog entry already links
one (M28). A named **Highlight** marked once on that full-flight timeline is the spine that drives
the **Summary** video, the vertical **Shorts**, and the YouTube metadata exposed over an API.

Flight-log data (site, glider, IGC analytics) is *not* in this app: it lives in the separate
**Flightlog** service (`https://fl.lenti.cloud`), which VidFactory calls over Flightlog's
`/api/integration/v1` contract using each user's own API key. VidFactory has its own, differently
scoped `/api/integration/v1` that an external YouTube-management tool calls *into* — don't conflate
the two.

It is a browser app deployed only as a Docker container. All heavy lifting is FFmpeg.

---

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry (`pyproject.toml`, **no lock file** — see below) |
| Relational DB | SQLite via SQLAlchemy 2.0 (no Alembic — sequential `.sql` migrations applied by `db.py`) |
| Video/audio processing | FFmpeg + ffprobe as subprocesses (no MoviePy / Python bindings) |
| YouTube download | `yt-dlp` (Python API, M28) |
| GPU encode | NVENC → Intel QSV (`/dev/dri`) → libx264, auto-detected (`sdh` runs `h264_qsv`) |
| Background jobs | In-process registry + one global FIFO queue with a single worker (`core/jobs.py`); progress via SSE |
| Flightlog client | `httpx` (`core/flightlog_client.py`) |
| Config | YAML (`config.yml`) validated by Pydantic; mount roots overridable via `VF_*` env vars |
| Frontend | Jinja2 + Tailwind (CDN) + HTMX; plain JS in `static/` (Alpine.js is loaded in `base.html` but unused) |
| Container | Docker; image published to GHCR on every `v*` tag; deployed from a separate IaC repo onto the `sdh` host |

### Releasing

Bump `version` in `pyproject.toml` **only** — `vidfactory.__version__` (dashboard, `/health`, OpenAPI)
is read from the installed package metadata (fixed after `v0.4.18` shipped reporting `0.4.17`
because it used to be a second hardcoded copy). A bare source checkout (`PYTHONPATH=src`) reports
`0+unknown`. Then commit, tag `vX.Y.Z` (must match), push — the tag triggers the image build.

### Dependency versioning (added 2026-08-19, after the upload 500 incident)

**No `poetry.lock` is committed** — every image build runs a fresh `poetry install` against
`pyproject.toml`'s constraints, so dependencies float to the latest compatible release. When adding
or bumping a dependency, use `>=<current>,<next-major>` rather than a bare `^`. **Poetry's caret on
`0.x` packages** (`^0.30`, `^0.0.9`) locks to that exact minor/patch and silently never updates,
even without a lock file — that is how the app shipped stuck on a `python-multipart==0.0.9`
boundary-parser bug (M9). `fastapi`, `uvicorn`, `httpx`, `python-multipart` are pre-1.0 and use an
explicit `<1.0` ceiling. `yt-dlp` uses date-based versions with no major, so it has a floor only
(and floats deliberately — YouTube breaks extractors often). Don't add a dependency that never gets
imported. No CI step runs `pytest` before `docker-publish.yml` builds/pushes an image (tag push →
straight to GHCR): a dependency bump is only verified by a local run or a manual post-deploy check.

---

## Repository Layout

```
src/vidfactory/
├── api/
│   ├── main.py            # app factory + lifespan (startup logging), /health, request middleware
│   ├── auth_deps.py       # require_user (session cookie), require_api_user (Bearer key)
│   ├── templating.py
│   └── routers/           # auth, browser, integration, projects, sse, youtube  (one per domain)
├── core/                  # FFmpeg engine + services
│   ├── ffmpeg_runner.py   # subprocess wrapper; stderr progress → job progress
│   ├── gpu_detector.py    # NVENC → QSV → libx264
│   ├── concat.py          # parts (+ sped-up hike) → full flight; 720p preview proxy
│   ├── highlights.py      # merge_overlaps / auto_fill
│   ├── summary.py         # Summary + full-flight-with-music, CTA end screen
│   ├── shorts.py          # highlight-driven + random-pool shorts, UsedMap de-dup
│   ├── music.py           # music bed, loudnorm/amix, credits
│   ├── youtube_meta.py    # metadata API payload (chapters/highlights/shorts/credits)
│   ├── youtube_download.py# M28: Flightlog "Full Flight" link → yt-dlp download
│   ├── flightlog_client.py / flightlog_hints.py   # Flightlog API + editor timeline hints
│   ├── chunked_upload.py  # resumable 8 MiB-chunk source upload
│   ├── filebrowser.py     # server-side browser over the mount roots (traversal guard)
│   ├── projects.py        # project CRUD helpers, output paths, shorts pruning
│   ├── auth.py            # scrypt password hashing, sessions, bootstrap user
│   └── jobs.py            # global job queue + registry
├── database/              # models.py (ORM), db.py (init_db, WAL, migrations), migrations/*.sql
├── models/                # Pydantic schemas (flightlog, highlight, youtube)
└── config.py              # Pydantic-validated YAML config + env overrides (singleton via get_config())
templates/  static/        # Jinja + HTMX UI; editor.js (highlight editor), jobs.js, browser.js, app.js
scripts/create_user.py     # add a second user (no signup UI)
resources/                 # CTA background image, fonts.conf, music license PDF
tests/backend/             # pytest (see 06-testing-conventions.md)
```

---

## Data Flow

```
Project created (POST /projects/new) — optional Flightlog flight id
  full flight comes from ONE of:
    • uploaded source parts ──concat (job)──▶ DATE_PID_FullFlight.mp4   (single part: plain copy)
    • Flightlog "Full Flight" YouTube link ──yt-dlp (job)──▶ same path    (M28, footage-less projects)
  full flight ──▶ 720p preview proxy (job) for the browser editor
  editor: mark named Highlights (optional launch/landing roles, picture highlights)
     ├─ Summary  (job, music, CTA)     ──▶ *_Summary.mp4 + _MusicCredits.txt
     ├─ Full flight + music (job)      ──▶ *_FullFlight_withMusic.mp4
     ├─ Shorts   (one job per short)   ──▶ *_Short_NN.mp4, history in DB
     └─ GET /api/projects/{id}/youtube-metadata  (also under /api/integration/v1, API-key auth)

Every job goes through one global FIFO queue (one at a time); the browser follows it over SSE
and on the /jobs page. Outputs are written to the configured output roots on the host.
```

---

## Data Sources

| Source | Auth | Key data | When |
|---|---|---|---|
| Uploads (`vf_uploads` volume, `VF_UPLOADS_DIR`) | session login | raw source footage, hike footage, picture-highlight images | chunked upload from the browser |
| Music library (`VF_MUSIC`, read-only) | — | StreamBeats tracks (license PDF in `resources/`) | read at Summary/Shorts build |
| Output roots (`VF_OUTPUT_*`, `VF_ARCHIVE`) | — | full flights, summaries, shorts | written by jobs, browsable/deletable in the file browser |
| SQLite (`vf_data` local volume → `/app/data/vidfactory.db`) | — | everything relational | never on NAS/NFS (WAL) |
| Flightlog (`VF_FLIGHTLOG_URL`, prod `https://fl.lenti.cloud`) | per-user `X-API-Key` | flight metadata, IGC segments, `links` (M28: the YouTube full flight) | best-effort, per request |
| YouTube | none | the full-flight video (M28) | on user confirmation, via `yt-dlp` |

---

## Users

Two users max, session-cookie login (`scrypt`), no signup UI: the first comes from
`VF_BOOTSTRAP_USERNAME`/`_PASSWORD` at first boot, more via `scripts/create_user.py`. Every project,
job and output is scoped to its owner (someone else's resource 404s, never 403). Each user also has
their own Flightlog key and their own integration API key, both set on `/account`.
