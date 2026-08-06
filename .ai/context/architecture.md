# Architecture Reference

This is the source-of-truth data model and contract for VidFactory. Update it whenever the schema,
pipeline, or deployment changes. (No InfluxDB and no in-app auth — see `01-project-overview.md`;
note in-app auth is now on the roadmap, see `features.md` "Multi-user".)

## Core concept

**Project is the top-level record.** Flight-log/logbook data (site, glider, date, IGC analytics)
used to live in this app as the **Outing** table but has moved to a separate **Flightlog**
project/service; `Project.external_flight_id` is an opaque, unenforced, currently-unused reference
to that service's future API (`core/flightlog_client.py`), and `Project.date` is the app's own
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
| `projects` | `id`, `date` (nullable), `external_flight_id` (nullable, opaque — no FK), `flight_type`('normal'\|'hike_and_fly'), `full_flight_file`, `preview_file` (720p proxy), `summary_file`, `fullflight_music_file`, `youtube_metadata_file`, `created_at` | **top-level**; standalone (migration 0006 added `date`/`external_flight_id`, replacing the old `outing_id` FK — see Core concept above). Created via `POST /projects/new` |
| `source_parts` | `id`, `project_id→projects`, `file`, `order` | raw source video parts (NAS-browsed **or uploaded**, see Storage & mounts), user-orderable |
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

3. **Shorts** (`shorts.py`) — all clips trimmed from the **full flight**, normalized to vertical
   1080×1920 (horizontal source: `crop=ih*9/16:ih…,scale=1080:1920`; **`setsar=1:1`+`fps=30` on every
   stream**; one `-i` per clip; CTA = looped image + `anullsrc`).
   - **Highlight-driven:** `highlight (hook) → launch? → random flying parts → landing? → CTA`, kept
     **under 30 s**; short title = highlight name. (`launch`/`landing` skipped if not marked.)
   - **Random-pool (legacy):** `launch? → flying → landing? → CTA`; random sampling with chronological
     re-sort and a `UsedMap` (per-file consumed ranges) so a batch never reuses footage.
4. **YouTube artifacts** (`youtube_meta.py`, not yet built) — `metadata.json` (+ readable `.txt`):
   chapters from highlights (`MM:SS Name`, first `00:00`, enforce YouTube ≥3 chapters / ≥10 s);
   summary + per-short titles/descriptions (site/glider, once available via
   `core/flightlog_client.py` — the Flightlog API doesn't exist yet, so this may ship without it
   and backfill later); suggested release dates (2–3 Shorts/wk); aggregated music credits. Consumed
   downstream by the YTChannelMgmt MCP.

**Music** (`music.py`) — folder mode (shuffle to cover duration; durations cached by folder-path hash)
or single-file mode (loop). `loudnorm` (EBU R128) then `amix` (`duration=first`, `normalize=0`) at a
user music/original ratio. Writes a credits `.txt` listing every track used.

---

## API Contracts (implemented through M4)

Pages return HTML (`include_in_schema=False`); mutations mostly reply `204 + HX-Redirect`; FFmpeg
builds return `{job_id}` and stream progress over SSE. Routers under `api/routers/`:

**main** — `GET /` dashboard · `GET /health` (liveness: 200 unless DB down; mounts reported as degraded).

**browser** — `GET /browse/{root}` page · `GET /api/browse/{root}?path=` (HTMX listing) ·
`GET /api/thumb/{root}?path=` (jpeg).

> Flight-log (`/flightlog*`) has been removed — see Core concept above and `features.md` M1a. There
> is no `flightlog` router anymore; those routes 404.

**projects** — `POST /projects/new` (create+redirect; optional `date` form field, defaults to today) ·
`GET /projects/{id}` page · `GET /projects/{id}/editor` page ·
flight-type/parts(add,upload,move,delete)/hike mutations ·
`POST /api/projects/{id}/parts/upload` (multipart, one or more files; streamed in 8 MB chunks to
`VF_UPLOADS_DIR/{project_id}/`, then added as `SourcePart`s — the local-disk alternative to browsing
the NAS) · `GET /api/projects/{id}/fullflight/video` (range stream; serves 720p `preview_file` if
present) · highlight CRUD `GET/POST /api/projects/{id}/highlights[/{hid}[/delete]]` (JSON) ·
builds (return `{job_id}`): `POST /api/projects/{id}/{build,preview/build,summary/build,fullmusic/build,shorts/build}` ·
`GET /api/projects/{id}/status`.

**sse** — `GET /events/{job_id}` (EventSource: `{stage,percent,speed,status,result}`) ·
`GET /api/jobs` · `POST /api/jobs/{id}/cancel`.

**Not yet built (M5):** `youtube` router — generate/download `metadata.json` (chapters/titles/desc/release/credits).

---

## Storage & mounts

The share `//172.18.10.10/pg` is **NFS-mounted** on the (now-retired) Fedora host at `/mnt/pg` and
bound into the container at `/data`. **This NAS is currently unreachable** (home network/host
changed after a move) — the app degrades gracefully (see health check below), but `VF_VIDEOS` browse
is effectively unusable until it's back. Roots are env-configurable; actual folder names on the share:

| Env | Value | Use |
|---|---|---|
| `VF_VIDEOS` | `/data/InstaOut` | source video parts (read) — **NAS-dependent, currently down** |
| `VF_MUSIC` | `/data/Music` | music library (read) — **not on the share yet** |
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
the NAS is unreachable. Layout: `{uploads_dir}/{project_id}/{filename}`. Uploaded parts are kept
indefinitely (no cleanup-after-build); the new host has 55 TB, so this isn't a near-term concern.

---

## Deployment

> **Stale — host retired.** Everything below (GPU/CDI specifics, Traefik labels, `xpsex` SSH deploy)
> described the old Fedora XPS host, which is no longer reachable after a move. Deploy target is a
> new host (55 TB storage) not yet detailed here; see `features.md` "Host migration" roadmap item.
> Kept as reference until that migration happens and this section gets rewritten for the new host.

Mirrors `C:\git\LSMFAPI`; registered in the `C:\git\lg4.ch` management repo. Domains: **`vf-dev.lg4.ch`**
(dev) and **`vf.lg4.ch`** (prod). Image: `ghcr.io/think4dvantage/vidfactory:vX`.

### GPU
Host (old XPS laptop) has both an NVIDIA RTX 3070 Mobile (4 GB) and Intel graphics. The prod compose
requests **both** paths so the detector can pick the best: NVIDIA via
`deploy.resources.reservations.devices` (`driver: nvidia`, `capabilities: [gpu]`) and Intel via
`/dev/dri` + render `group_add`. Ansible (`run_ansible: true`) installs the NVIDIA Container Toolkit
and the CIFS `pg` mount.

### Traefik Label Format
Homelab requires **list format** labels (not map). Add `traefik.docker.network=proxy` when on multiple
networks.

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.docker.network=proxy"
  - "traefik.http.routers.vidfactory.rule=Host(`vf.lg4.ch`)"
  - "traefik.http.routers.vidfactory.entrypoints=websecure"
  - "traefik.http.routers.vidfactory.tls.certresolver=letsencrypt"
  - "traefik.http.services.vidfactory.loadbalancer.server.port=8000"
```

### Healthcheck
`python:3.11-slim` has no `curl`; use Python stdlib:

```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
```

### Dev overlay
`docker-compose.dev.yml` extends the base with live `src/`+`templates/`+`static/` mounts (`:ro,z`),
the `proxy` network + Traefik labels for `vf-dev.lg4.ch`, and `PYTHONPYCACHEPREFIX=/tmp/pycache`.
Dev deploy via `scripts/VF-dev.ps1` (tar → ssh `xpsex` → `/opt/VidFactory` → `docker compose … up --build -d`).
