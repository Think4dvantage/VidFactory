# Architecture Reference

This is the source-of-truth data model and contract for VidFactory. Update it whenever the schema,
pipeline, or deployment changes. (No InfluxDB. In-app auth **shipped** — see `features.md` M5b —
opaque session-cookie login, two users max, no signup UI.)

## Core concept

**Project is the top-level record**, owned by exactly one `User` (`owner_id`; M5b). Flight-log/
logbook data (site, glider, date, IGC analytics) used to live in this app as the **Outing** table
but has moved to a separate **Flightlog** project/service, which now exposes a live, frozen
`/api/integration/v1` contract (M6). `Project.external_flight_id` (`TEXT`, matches Flightlog's own
string flight id) is that service's flight id; `core/flightlog_client.py` calls it with the owning
user's own `User.flightlog_api_key` (set on `/account`, never returned in any response) against
`config.flightlog.base_url` / `VF_FLIGHTLOG_URL`. `Project.date` is the app's own
minimal flight date (set at creation, editable). A **Highlight** is marked once on the assembled
full-flight timeline and is the shared spine: it produces summary segments, YouTube chapters, and
named-short sources. `launch` and `landing` are just Highlights with a `role` and are **optional**
(the camera isn't always rolling at launch / battery may die before landing).

> The live SQLite DB still physically contains the old `outings`, `sites`, `lookups`, `buddies`,
> `outing_buddies`, and `igc_tracks` tables (598 outings, 281 igc_tracks rows) — they are **not
> dropped**, since that data seeds the separate Flightlog project. This app's code no longer maps
> or queries them at all; treat them as foreign/legacy on this DB file.

---

## SQLite Tables (SQLAlchemy ORM in `database/models.py`)

| Table | Key columns | Notes |
|---|---|---|
| `users` | `id`, `username`(unique), `password_hash`(`scrypt$salt$digest`), `flightlog_api_key`(nullable, plaintext bearer credential), `created_at` | No roles/signup UI — bootstrap user from `VF_BOOTSTRAP_USERNAME`/`_PASSWORD` at first boot, additional users via `scripts/create_user.py` |
| `sessions` | `token`(PK, opaque), `user_id→users`, `created_at`, `expires_at` | ~30-day cookie session (`vf_session`, httponly); logout deletes the row |
| `projects` | `id`, `owner_id→users`(nullable), `date` (nullable), `external_flight_id` (nullable `TEXT`, Flightlog's flight id — no FK, separate service), `flight_type`('normal'\|'hike_and_fly'), `full_flight_file`, `preview_file` (720p proxy), `summary_file`, `fullflight_music_file`, `youtube_metadata_file`, `created_at` | **top-level**; standalone (migration 0006 added `date`/`external_flight_id`, replacing the old `outing_id` FK; 0007 added `owner_id`; 0008 retyped `external_flight_id` to `TEXT` — see Core concept above). Created via `POST /projects/new`. A project not owned by the requesting user 404s (never 403) |
| `source_parts` | `id`, `project_id→projects`, `file`, `order` | raw source video parts (uploaded — see Storage & mounts, M7), user-orderable |
| `hikes` | `id`, `project_id→projects`, `sources`(json list), `speed_factor`(default 32.0) | H&F only; sped up and prepended to the full flight |
| `highlights` | `id`, `project_id→projects`, `name`, `start`, `end`, `comment`, `type`('video'\|'picture'), `role`('normal'\|'launch'\|'landing'), `image_path`(nullable), `duration`(nullable, picture), `use_in_summary`(bool), `make_short`(bool) | **the spine**; times on the full-flight timeline. At most one `launch` and one `landing`, both optional |
| `pools` | `id`, `project_id→projects`, `kind`('flying'\|'hiking'), `start`, `end` | ranges **on the full flight** for random short sampling (legacy mode) |
| `shorts` | `id`, `project_id→projects`, `output_file`, `short_type`, `created_at`, `duration`, `segments_used`(json), `source_highlight_id`(nullable→highlights), `title`(nullable) | generation history; `segments_used` records exact source ranges per section |

**Migrations:** no Alembic. Per `02-backend-conventions.md`, schema changes are sequential idempotent
`.sql` files in `database/migrations/` (`0001_initial_schema.sql`, …), applied by `db.py:init_db()`
and tracked in a `_migrations` table; SQLite runs in WAL mode. The DB file is `vidfactory.db` on the
**local `vf_data` docker volume** (`VF_DATA_DIR=/app/data` → `/app/data/vidfactory.db`), never on the
NAS — WAL is unsafe over NFS/SMB (see Storage & mounts below). NB: a stale legacy copy may linger at
`/data/Archive/vidfactory.db` on the host; the app does **not** use it — always inspect `/app/data`.

---

## FFmpeg pipeline (in `core/`)

All FFmpeg/ffprobe calls are subprocesses through `ffmpeg_runner.py`; long runs are background jobs
reporting progress over SSE. Encoder is chosen by `gpu_detector.py`: **NVENC → QSV (`/dev/dri`) →
libx264**. `FONTCONFIG_FILE` points at `resources/fonts.conf` so `drawtext` resolves fonts.

1. **Concat → Full Flight** (`concat.py`) — FFmpeg concat demuxer over ordered `source_parts`; for
   H&F, speed up `hikes.sources` (`setpts` + chained `atempo`) and **prepend**. → `DATE_FullFlight.mp4`.
2. **Summary** (`summary.py`) — dedup/merge overlapping highlights; **auto-fill** evenly-distributed
   non-overlapping filler to reach target length; single-pass `filter_complex` (`trim`/`atrim` per
   highlight, `drawtext` overlay = highlight name, `concat=n=…:v=1:a=1`); picture highlights via
   `-loop 1 … anullsrc`. Mix music (see below) → `DATE_Summary.mp4` + credits. Separate
   full-flight-with-music action stream-copies video and mixes music → `DATE_FullFlight_withMusic.mp4`.
**Editor proxy:** the highlight editor streams a **720p proxy** (`preview_file`, same timeline as the
4K full flight) for smooth browser scrubbing; built by `concat.build_preview` (short GOP + faststart)
automatically after the full flight, or on demand. IN/OUT marks map 1:1 to the 4K source.

**Flightlog timeline hints (M8):** the editor canvas (`static/editor.js`) also overlays
Flightlog-derived markers — thermal starts today, any future `SegmentOut.kind` automatically (no
hardcoding, see `core/flightlog_hints.py`). Anchored on the pilot's own `launch` highlight (its
`start` = takeoff on the video timeline; Flightlog's `start_offset_s` is seconds-since-takeoff with
no camera-start concept of its own), so hints only appear once a launch highlight exists —
`GET /api/projects/{id}/flightlog-hints` reports `no_launch_marked` until then. Hover a marker for
a tooltip (kind/alt-change/climb-rate), click near one to seek exactly to it.

3. **Shorts** (`shorts.py`) — all clips trimmed from the **full flight**, normalized to vertical
   1080×1920 (horizontal source: `crop=ih*9/16:ih…,scale=1080:1920`; **`setsar=1:1`+`fps=30` on every
   stream**; one `-i` per clip; CTA = looped image + `anullsrc`).
   - **Highlight-driven:** `highlight (hook) → launch? → random flying parts → landing? → CTA`, kept
     **under 30 s**; short title = highlight name. (`launch`/`landing` skipped if not marked.)
   - **Random-pool (legacy):** `launch? → flying → landing? → CTA`; random sampling with chronological
     re-sort and a `UsedMap` (per-file consumed ranges) so a batch never reuses footage.
4. **YouTube metadata** (`core/youtube_meta.py`, M5a — shipped as a live read API, not a written
   `metadata.json`, see `## API Contracts` → **youtube** below): highlights, summary content order,
   shorts, and — since M6 — Flightlog flight/segment data when the project has one linked, for a
   separately-run YouTube-management container to derive chapters/titles/descriptions/release plan
   from on its own pull schedule.

**Music** (`music.py`) — folder mode (shuffle to cover duration; durations cached by folder-path hash)
or single-file mode (loop). `loudnorm` (EBU R128) then `amix` (`duration=first`, `normalize=0`) at a
user music/original ratio. Writes a credits `.txt` listing every track used.

---

## API Contracts (implemented through M8)

Pages return HTML (`include_in_schema=False`); mutations mostly reply `204 + HX-Redirect`; FFmpeg
builds return `{job_id}` and stream progress over SSE. Every route except `GET /health` and
`/static/*` requires login (`api/auth_deps.require_user`, a normal `Depends` so
`app.dependency_overrides` reaches it in tests) — `/api/*` paths get a 401
`{"error":{"code":"AUTH_REQUIRED",...}}` JSON body, page routes get a 303 redirect to `/login`.
Routers under `api/routers/`:

**main** — `GET /` dashboard (own projects/jobs only) · `GET /health` (liveness: 200 unless DB
down; mounts reported as degraded; deliberately public — gates Traefik routing).

**auth** — `GET/POST /login` · `POST /logout` · `GET /account` page + `POST
/api/account/flightlog-key` (sets the current user's Flightlog API key; the page only ever shows
"configured: yes/no", never the key itself).

**browser** — `GET /browse/{root}` page · `GET /api/browse/{root}?path=` (HTMX listing) ·
`GET /api/thumb/{root}?path=` (jpeg). Login-gated but **not** ownership-scoped — the music mount is
a shared household resource, not per-user. **M7:** `videos` is no longer a browsable root at all
(see Storage & mounts) — `music` is the only one left.

> Flight-log (`/flightlog*`) has been removed — see Core concept above and `features.md` M1a. There
> is no `flightlog` router anymore; those routes 404.

**projects** — `POST /projects/new` (create+redirect; optional `date` form field, defaults to today) ·
`GET /projects/{id}` page · `GET /projects/{id}/editor` page ·
flight-type/`flightlog-id`/parts(upload,move,delete)/hike(`upload`,`remove`) mutations ·
`POST /api/projects/{id}/parts/upload` (multipart, one or more files; streamed in 8 MB chunks to
`VF_UPLOADS_DIR/{project_id}/`, then added as `SourcePart`s — the **only** way to get source
footage in, see Storage & mounts) · `POST /api/projects/{id}/hike/upload` (multipart, same chunked
upload to `VF_UPLOADS_DIR/{project_id}/hike/`, appends to `Hike.sources` + sets `speed_factor`) ·
`POST /api/projects/{id}/hike/remove` (form `index`, removes one `Hike.sources` entry by position) ·
`GET /api/projects/{id}/fullflight/video` (range stream; serves 720p `preview_file` if
present) · highlight CRUD `GET/POST /api/projects/{id}/highlights[/{hid}[/delete]]` (JSON) ·
`GET /api/projects/{id}/flightlog-hints` (M8, `core/flightlog_hints.py`) — segment-derived timeline
hints for the highlight editor, `{"status": "no_launch_marked"|"unavailable"|"ok", "hints": [...]}`;
never errors, always 200 (see Core concept / Storage & mounts style best-effort pattern) ·
builds (return `{job_id}`): `POST /api/projects/{id}/{build,preview/build,summary/build,fullmusic/build,shorts/build}` ·
`GET /api/projects/{id}/status`.

**sse** — `GET /events/{job_id}` (EventSource: `{stage,percent,speed,status,result}`) ·
`GET /api/jobs` · `POST /api/jobs/{id}/cancel`. All three scoped to the caller's own jobs
(`core/jobs.Job.owner_id`, set when `registry.run()` is called) — a job that exists but belongs to
someone else 404s, same as a project.

**youtube** (M5a, `core/youtube_meta.py`) — `GET /api/projects/{id}/youtube-metadata`: read-only,
`response_model`-typed (visible in `/docs`, unlike the editor's internal JSON endpoints), 404 via the
`07-api-conventions.md` `ENTITY_NOT_FOUND` error shape. Returns project fields + every `Highlight`
(full-flight timestamps, ground truth) + `segment_order` (content order/cumulative duration from
`highlights.merge_overlaps()` over `use_in_summary` highlights — **not** a rendered Summary.mp4
timestamp, see the docstring in `models/youtube.py`) + every `Short` with `segments_used` and its
resolved `source_highlight_name` (null for random-pool shorts). No `metadata.json` file is written —
API-only per the live-pull model an external YouTube-management container uses; that container derives
actual chapters/titles/descriptions itself from this raw data. **M6:** also `flight` +
`flight_segments` (`models/flightlog.py`, mirrors Flightlog's contract verbatim) — best-effort;
`None` whenever `external_flight_id`/the owner's API key isn't set or the Flightlog call errors,
logged at INFO and never raised into this endpoint. `POST /api/projects/{id}/flightlog-link`
(body `{youtube_url, label?}`) forwards to Flightlog's `PUT .../links/video/{project_id}` —
called by the external YTChannelMgmt MCP once it actually publishes a video (VidFactory's own
outputs are local files with no public URL, so there's no automatic trigger after a render).

---

## Storage & mounts

The share `//172.18.10.10/pg` is **NFS-mounted** on the (now-retired) Fedora host at `/mnt/pg` and
bound into the container at `/data`. **This NAS is unreachable for the next year** (home
network/host changed after a move) — see M7 below. Roots are env-configurable; actual folder names
on the share:

| Env | Value | Use |
|---|---|---|
| `VF_MUSIC` | `/data/music` | music library (read) — **not on the share yet**; standalone deploy bind-mounts a host folder instead (see below) |
| `VF_OUTPUT_SUMMARIES` | `/data/summaries` | summaries + full-flight-with-music + credits (write) |
| `VF_OUTPUT_SHORTS` | `/data/shorts` | shorts (write) |
| `VF_ARCHIVE` | `/data/Archive` | metadata.json + credits (write) |
| (full flights) | `/data/fullflights` | concat output (wired in M2) |

**The SQLite DB lives on a LOCAL docker volume** (`VF_DATA_DIR=/app/data`, volume `vf_data`), **not on
the NAS** — SQLite WAL mode does not work over NFS/SMB. The NAS `archive` root is only for produced
artifacts (metadata.json, credits). Startup health check verifies each root; a missing mount is
reported as `degraded` (HTTP 200, app stays reachable) — see `08-operability.md`.

**Uploaded raw video** lives on its own LOCAL docker volume, `vf_uploads` (`VF_UPLOADS_DIR=/app/uploads`,
config `uploads_dir`) — deliberately **not** the NAS mount, since the whole point is to work while
the NAS is unreachable. Layout: `{uploads_dir}/{project_id}/{filename}` for ordinary source parts,
`{uploads_dir}/{project_id}/hike/{filename}` for Hike & Fly footage. Uploaded parts are kept
indefinitely (no cleanup-after-build); the new host has 55 TB, so this isn't a near-term concern.

**M7 (2026-08-19): the `videos`/`VF_VIDEOS` mount root is gone entirely** — the NAS it pointed at
(`InstaOut`) won't be reachable for the next year, so `config.MountsSection` no longer has a
`videos` field and `mount_roots()`/`check_mounts()` never mention it. Upload
(`POST /api/projects/{id}/parts/upload`, above) is now the **only** way to add source footage —
there is no NAS-browse fallback any more, not a degraded one. The generic `browser`/`filebrowser`
mechanism itself is untouched and still serves `music` (see `## API Contracts` → **browser**),
which was already local-only in the standalone deploy and unaffected by the NAS being gone.

**Standalone deploy (M1b)** — `docker-compose.standalone.yml`, for running fully independently of
the NAS/Traefik/`xpsex` while the real deploy host is TBD (see Deployment below). It bind-mounts two
host folders instead of `/data`: `VF_MUSIC_HOST` (read-only, folder mode for `music.py`) → `VF_MUSIC`,
and `VF_LIBRARY_HOST` → the four write roots (`VF_OUTPUT_FULLFLIGHTS/SUMMARIES/SHORTS`, `VF_ARCHIVE`)
as subfolders, so finished output lands on a real host path instead of a docker volume. `VF_VIDEOS` is
left unmounted (browse degrades to "missing", non-fatal; upload is the working path). Nothing in
`config.py`/`music.py` changed — both already took arbitrary paths via env override; this is
compose+env only. `.env.example` documents the two host-path vars (compose auto-loads `.env`).

---

## Deployment

**This repo does not deploy itself.** It produces three things for whatever project/host actually
runs the container: the image (built + pushed to GHCR by `.github/workflows/docker-publish.yml` on
every `v*` tag — `ghcr.io/think4dvantage/vidfactory:latest` and `:vX.Y.Z`, currently `v0.4.2`), and
two example config files to copy over — `docker-compose.standalone.yml` and `.env.example`
(alongside the existing `config.yml.example`). The actual deploy target — the shared docker host at
SSH alias `sdh` (55 TB pooled storage, GPU = dedicated Intel card, QSV via `/dev/dri`; NVIDIA CDI
device present in the example but commented out, no NVIDIA Container Toolkit there) — is managed
from another project (the user's separate Docker-host repo, not this one), see the host-migration
findings below. No Traefik/`proxy` network in the example — it publishes `8000:8000` directly (the
real `sdh` compose file adds its own Traefik labels on top, see below). See "Standalone deploy
(M1b)" above for the mount rationale. `VF-dev.ps1` is unrelated to this path (still hardwired to
`xpsex`, which is dead — see below).

**Repo/CI state:** on GitHub at `Think4dvantage/VidFactory`, branch `main` (pushed — not the old
local-only `m0-foundation` branch `features.md`'s deploy blockquote used to describe). Tags
`v0.2.0`–`v0.4.2` so far, each auto-publishing the image on push.

**Host migration has happened (found 2026-08-19, not yet reflected anywhere else in these docs
before now).** `xpsex`/`lg4.ch` are dead and irrelevant — the app is live on a **different** shared
docker host, SSH alias `sdh`, compose project at `/opt/sdh.lol/compose/public/vidfactory/compose.yml`
(project name `vidfactory`, not managed from this repo — same "another project owns deploy" model as
before, just a new host). Confirmed via `docker inspect`/`docker logs`/`curl :8000/health` on `sdh`:

- **Domain**: `vf.lenti.cloud` (**not** `vf.lg4.ch`) — Traefik labels in **map** format here (this
  homelab differs from the old lg4.ch one), HTTP router redirects to HTTPS
  (`redirect-to-https@file` middleware) and the HTTPS router terminates via `letsencrypt`.
- **Storage**: bind mounts, not the `vf_uploads`/named-volume pattern the repo's own compose
  examples use — `/mnt/media/vidfactory/{uploads,music,library}` → `/app/{uploads,music,library}`,
  on a pooled/cache array (`cache:disk1:disk2:disk3:disk4`, 54 T total / 41 T free at last check).
  This is the "new (55 TB) host" the roadmap referred to.
- **GPU**: host has a single Intel Arc A380 (DG2, discrete — `lspci` shows no separate integrated
  GPU; likely disabled in BIOS or absent on this CPU) at `/dev/dri`, correctly passed through and
  VAAPI-healthy (`iHD` driver loads fine, confirmed via `ffmpeg -init_hw_device qsv=hw` debug
  output). The actual `h264_qsv` encode still failed (`Error creating a MFX session: -9`) because
  the image was missing `libmfx-gen1.2` — the oneVPL GPU runtime `h264_qsv` needs on top of
  `va-driver-all`'s VAAPI layer; Jellyfin on the same host works because it bundles its own
  self-contained `jellyfin-ffmpeg` and never needed this package. Confirmed as the actual fix by
  installing it live in the running container (ephemeral, not persisted) and re-running the exact
  failing encode — clean exit. Fixed for good in `Dockerfile` (not yet built/deployed).
- **Flightlog reachability**: `VF_FLIGHTLOG_URL=http://flightlog:8000` — a sibling container on the
  same docker network, so the M6 Flightlog integration (previously never live-tested — outbound
  egress from the dev sandbox that built it was blocked) **can now actually be verified live** from
  this host, unlike the old dev-sandbox limitation.
- Running image tag `ghcr.io/think4dvantage/vidfactory:0.4.0` at last check (2026-08-19), revision
  label matched `main` HEAD (`55bf64d`) at that point — i.e. current as of then, but **predates the
  M9 fix** (that shipped as `v0.4.1`); `sdh` has not been redeployed since. `/health` at last check:
  `{"status":"ok", "sqlite":"ok", every mount "ok", "jobs_active":0}`.
- **Not yet verified beyond base health**: login, uploads (the M9 `python-multipart` fix isn't live
  on `sdh` until it's redeployed to `v0.4.1`+), builds, and a real Flightlog API call. Only
  `/health` + container/mount state were checked so far.

Latest published image: `ghcr.io/think4dvantage/vidfactory:0.4.2`; `sdh` was running `0.4.1` (the
M9 upload fix, not yet the M10 GPU fix) as of the last check above. See `features.md` "Host
migration" roadmap item.

### Healthcheck
`python:3.11-slim` has no `curl`; use Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
```

### Dev overlay — stale, describes the dead `xpsex`/lg4.ch host, not `sdh`
`docker-compose.dev.yml` extends the base with live `src/`+`templates/`+`static/` mounts (`:ro,z`),
the `proxy` network + Traefik labels for `vf-dev.lg4.ch`, and `PYTHONPYCACHEPREFIX=/tmp/pycache`.
Dev deploy via `scripts/VF-dev.ps1` (tar → ssh `xpsex` → `/opt/VidFactory` → `docker compose … up --build -d`).
Neither the compose overlay nor the script have been updated for `sdh` — there's currently no dev
deploy path to the new host, only whatever manages the prod `compose.yml` at
`/opt/sdh.lol/compose/public/vidfactory/` (outside this repo).
