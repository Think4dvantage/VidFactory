# Project Overview — VidFactory

## What This Is

VidFactory is a self-hosted web app that unifies a paragliding YouTube creator's whole post-flight
workflow into **one tool with one source of truth**. Today the same flight is described four times —
in a flight logbook (`Flugbuch.xlsx`), in a PowerShell summary-video tool (PS_VidAggregator), in a
Qt shorts tool (ShortFactory), and again in YouTube metadata (YTChannelMgmt). VidFactory replaces
that re-entry: the **Outing** (one flight) is the top-level record, it optionally owns a **video
project**, and a **named Highlight** marked once on the assembled full-flight video becomes the spine
that drives the summary, the shorts, and the YouTube metadata (chapters, titles, descriptions, SEO).
Its core differentiator is that **flight data and highlights are entered once and reused everywhere**.

It is a browser app, deployed only as a Docker container on the user's Fedora Docker host (behind the
lg4.ch Traefik reverse proxy). All heavy lifting is FFmpeg; large source videos (60–300 GB) are never
uploaded — they are read directly from a mounted NAS share.

---

## Tech Stack

| Concern | Tool |
|---|---|
| Language | Python 3.11+ |
| Web framework | FastAPI |
| Data validation | Pydantic v2 |
| Dependency management | Poetry (`pyproject.toml`) |
| Relational DB | SQLite via SQLAlchemy (no Alembic — lightweight column migrations in `db.py`) |
| Video/audio processing | FFmpeg + ffprobe invoked as subprocesses (no MoviePy / Python bindings) |
| GPU encode | NVENC (RTX 3070 Mobile) → Intel QSV (`/dev/dri`) → libx264, auto-detected |
| Background jobs | FastAPI background tasks + an in-process job registry; progress via SSE |
| Spreadsheet import | openpyxl (one-time `Flugbuch.xlsx` ingest) |
| Config | YAML (`config.yml`) validated by Pydantic; mount roots via env vars |
| Frontend | Jinja2 templates + Tailwind (CDN) + HTMX; Alpine.js/vanilla JS only on the highlight editor |
| Container | Docker + docker-compose; deploy mirrors `C:\git\LSMFAPI` |

**Removed from the dev-web blueprint default (not applicable here):** InfluxDB (no time-series),
in-app JWT auth, and i18n. This is a single-operator tool; access is gated by the lg4.ch reverse proxy
(Pocket-ID) if exposed. The `.ai/` instruction/prompt files have been pruned of those topics.

---

## Repository Layout

```
src/vidfactory/
├── api/
│   ├── main.py              # FastAPI app factory + lifespan (startup health check)
│   └── routers/             # one file per domain: flightlog, projects, concat,
│                            #   summary, shorts, youtube, browser, sse
├── core/                    # FFmpeg-side engine + services
│   ├── ffmpeg_runner.py     # subprocess wrapper; stderr progress → SSE  (port: ShortFactory)
│   ├── gpu_detector.py      # NVENC→QSV→libx264 detection             (port: ShortFactory)
│   ├── probe.py             # ffprobe helpers (w/h/duration/fps)
│   ├── concat.py            # parts concat + sped-up hike prepend       (port: VideoConcatenator.ps1)
│   ├── summary.py           # highlight dedup/auto-fill + summary encode (port: VideoSummaryCreator.ps1)
│   ├── shorts.py            # highlight-driven + random-pool shorts      (port: short_builder.py)
│   ├── music.py             # folder-cache + loudnorm/amix mix + credits (port: PS music scripts)
│   ├── youtube_meta.py      # chapters/titles/descriptions/release/credits → metadata.json
│   ├── filebrowser.py       # server-side browser over mount roots, traversal guard, thumbnails
│   ├── flightlog.py         # outings CRUD + rollups (hours, site frequency, seasonality)
│   ├── sites.py             # sites master (27 launch / 27 landing + elevations)
│   ├── importer.py          # one-time Flugbuch.xlsx + legacy archive import
│   └── jobs.py              # background job registry + persisted progress
├── database/
│   ├── models.py            # SQLAlchemy ORM — source of truth for the schema
│   ├── db.py                # init_db() (WAL + apply migrations), get_db()
│   └── migrations/          # sequential .sql files, tracked via _migrations table
├── models/                  # Pydantic request/response schemas
├── config.py                # Pydantic-validated YAML config + env mount roots (singleton)
└── ...
resources/                   # EndScreenBackground.JPG, fonts.conf  (copied from ShortFactory)
templates/  static/          # Jinja + HTMX UI; highlight-editor JS
```

---

## Data Flow

```
Flugbuch.xlsx ──importer──▶ SQLite (Sites, Outings)         [one-time, then maintained in-app]

User marks an Outing ▶ (optional) Video Project
  source parts ──concat (FFmpeg job)──▶ Full Flight .mp4
  Full Flight ──mark named Highlights (incl. optional launch/landing roles)──▶
     ├─ Summary (FFmpeg job + music)        ──▶ DATE_Summary.mp4 + credits.txt
     ├─ Shorts  (FFmpeg jobs + music)       ──▶ *_Short_NN.mp4 (+ history)
     └─ youtube_meta                        ──▶ metadata.json (chapters/titles/desc/release/credits)

API routers query SQLite + drive FFmpeg jobs; the browser polls job progress over SSE.
Outputs are written to the mounted NAS share; metadata.json is handed to the YTChannelMgmt MCP.
```

---

## Data Sources

| Source | Auth | Key data | When |
|---|---|---|---|
| NAS SMB share `//172.18.10.10/pg` (`/data` in container) | host CIFS mount | source videos (`InstaOut/`), music (`Music/`), outputs (`Shorts/`, `Summaries/`), SQLite DB (`Archive/`) | read on demand; outputs written |
| `Flugbuch.xlsx` (from YTChannelMgmt) | local file | 598 flights 2018–2026 + 27/27 sites w/ elevations | one-time import, then in-app |
| YTChannelMgmt MCP | — | consumes the `metadata.json` artifact VidFactory writes | downstream handoff |

---

## Users

Single operator (the channel owner). No in-app role system; if the app is exposed publicly it is
protected at the reverse proxy (Traefik / Pocket-ID middleware on the lg4.ch host), not in code.
