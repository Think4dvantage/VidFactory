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
| `users` | `id`, `username`(unique), `password_hash`(`scrypt$salt$digest`), `flightlog_api_key`(nullable, plaintext bearer credential), `api_key`(nullable, unique, plaintext bearer credential — migration `0009`, see `## API Contracts` → **integration**), `created_at` | No roles/signup UI — bootstrap user from `VF_BOOTSTRAP_USERNAME`/`_PASSWORD` at first boot, additional users via `scripts/create_user.py` |
| `sessions` | `token`(PK, opaque), `user_id→users`, `created_at`, `expires_at` | ~30-day cookie session (`vf_session`, httponly); logout deletes the row |
| `projects` | `id`, `owner_id→users`(nullable), `date` (nullable), `external_flight_id` (nullable `TEXT`, Flightlog's flight id — no FK, separate service), `pilot_name` (nullable `TEXT`, migration `0010`), `flight_type`('normal'\|'hike_and_fly'), `full_flight_file`, `preview_file` (720p proxy), `summary_file`, `fullflight_music_file`, `youtube_metadata_file`, `created_at` | **top-level**; standalone (migration 0006 added `date`/`external_flight_id`, replacing the old `outing_id` FK; 0007 added `owner_id`; 0008 retyped `external_flight_id` to `TEXT` — see Core concept above). Created via `POST /projects/new`. A project not owned by the requesting user 404s (never 403). `pilot_name` (M21) is a manual free-text field for footage flown by someone with no Flightlog account of their own — a non-null value doubles as the flag that this isn't the owning user's own flight, surfaced as a badge on the dashboard and project page, and exposed via `youtube-metadata` so a downstream tool can credit the actual pilot |
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
   **M22:** a single source part with no hike needs no concat at all — `concatenate()` just copies
   it straight to the output path (`shutil.copy2`), skipping ffmpeg entirely for that case.
2. **Summary** (`summary.py`) — dedup/merge overlapping highlights; **auto-fill** evenly-distributed
   non-overlapping filler to reach target length; single-pass `filter_complex` (`trim`/`atrim` per
   highlight, `drawtext` overlay = highlight name, `concat=n=…:v=1:a=1`); picture highlights via
   `-loop 1 … anullsrc`. Mix music (see below) → `DATE_Summary.mp4` + credits. Separate
   full-flight-with-music action stream-copies video and mixes music → `DATE_FullFlight_withMusic.mp4`,
   written to `output_fullflights` alongside the plain full flight and preview (fixed 2026-08-20 —
   was landing in `output_summaries`; `core/projects.py:fullmusic_output_path`).
   **M25: both now end on the CTA end screen** (`Config.cta_image` + `ShortsSection.cta_duration`/
   `cta_line1`/`cta_line2` — the same "for more relaxed Paragliding / Like & Subscribe" screen
   `shorts.build_short` already appended). `build_summary()` folds it in as one more `filter_complex`
   concat input (looped image + `anullsrc` + two centred `drawtext` lines sized off the project's
   own height) — free, since Summary already re-encodes every segment in one pass; the music mix
   runs on the concat's combined `[outa]`, so music keeps playing under the CTA too.
   `build_fullflight_with_music()` can't do that cheaply — it deliberately stream-copies the (often
   multi-hour, 4K) video — so it instead encodes the CTA separately at the flight's own
   resolution/fps and joins it with the concat demuxer (`-c copy`), the same normalize-then-copy
   trick used to prepend a sped-up hike in step 1. Neither the CTA's screen time nor its (fixed,
   config-known) duration is reflected in `youtube_meta.py`'s `segment_order` yet — already a known
   approximation (see the M5-remainder roadmap row on exact chapter timestamps), just very slightly
   more so now.
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
   - **Highlight-driven:** `highlight (hook) → hike×2? → launch? → 3 random flying parts → landing? →
     CTA`; short title = highlight name. (`launch`/`landing` skipped if not marked; the hike pair only
     for `flight_type == "hike_and_fly"`, M15.) No longer reliably **under 30 s** once the hike pair is
     included (6+2×3+4+3×3+4+3 = 32s with defaults) — the M4 cap was superseded by this composition
     change; YouTube Shorts' own 60s ceiling is the only hard limit left.
   - **Random-pool (legacy):** same order minus the hook — `hike×2? → launch? → flying → landing? →
     CTA`; random sampling with chronological re-sort and a `UsedMap` (per-file consumed ranges) so a
     batch never reuses footage.
   - **M15 (2026-08-21): hike/flying split + distributed sampling.** `_flying_pool()`
     (`api/routers/projects.py`) now excludes the prepended hike segment from the flying pool
     (`core/concat.hike_output_duration()` recomputes the hike's on-timeline length — sped-up
     duration or raw sum — the same math `concatenate()` uses to prepend it, since that boundary was
     never persisted anywhere) — flying clips for a Hike & Fly short can no longer land inside the
     hike footage. Two hike clips are sampled from `[0, hike_end)` the same way flying clips are
     sampled from `[hike_end, full_dur)`. Root cause of the original complaint (all-random flying
     clips clustering in one small region of a 12.07.2026 project instead of spreading across the
     whole flight): `shorts.pick_clips()` picked among leftover *windows* with equal probability
     regardless of window size — once a region got used, subtraction fragmented it into many small
     windows that then outnumbered (and so out-competed) any single large untouched region, so later
     picks kept clustering wherever the first few picks happened to land, purely by chance, not by any
     size-weighting. Fixed by splitting the pool into `count` equal-width **time buckets** and
     drawing one subclip per bucket (falling back to the whole pool — logged — only if a bucket has
     no free room), guaranteeing genuine spread instead of relying on statistics. `launch`/`landing`
     highlights are user-marked and bypass this pool entirely; if one is mismarked inside the hike
     segment, `_short_target` (renamed from `_shorts_target` at M24 — see below) logs a warning
     (`"launch highlight starts ... before the hike segment ends"`) rather than silently clamping
     someone's manual mark. New `ShortsSection.hike_clip_count`
     (default 2). **Verified locally** (`tests/backend/test_shorts_engine.py`,
     `tests/backend/test_shorts_build.py`) — not yet re-verified against a real rebuild of the
     12.07.2026 project (needs a live host; not yet deployed).
   - **M24: one queued job per short.** `build_shorts_ep` resolves the batch's task list (hook
     range/title/highlight id for highlight mode, `count` blank tasks for random — DB-only, no
     ffprobe) and calls `registry.run("shorts", ...)` once per task instead of once for the whole
     batch, so the Job Queue page (M17) shows the real count of videos being produced instead of
     one row regardless of batch size. `_shorts_target`'s loop became `_short_target`, building
     exactly one short; the `UsedMap` de-dup that has to span the whole batch is created once in
     the endpoint and shared by closure across every task's job (safe since the M13 global queue
     runs one job at a time — those closures never run concurrently, just possibly interleaved
     with unrelated jobs from other projects/users). `Job` gained a `title` field (the highlight
     name, surfaced on `/api/jobs`/SSE/the queue page) purely for that per-row insight. Cancelling
     is now per-short rather than per-batch — a disclosed tradeoff, not a bulk-cancel control
     anyone has asked for.
4. **YouTube metadata** (`core/youtube_meta.py`, M5a — shipped as a live read API, not a written
   `metadata.json`, see `## API Contracts` → **youtube** below): highlights, summary content order,
   shorts, and — since M6 — Flightlog flight/segment data when the project has one linked, for a
   separately-run YouTube-management container to derive chapters/titles/descriptions/release plan
   from on its own pull schedule.

**Music** (`music.py`) — folder mode (shuffle to cover duration; durations cached by folder-path hash)
or single-file mode (loop). `loudnorm` (EBU R128) then `amix` (`duration=first`, `normalize=0`) at a
user music/original ratio. **M18:** credits are now a pasteable attribution block, not a bare
track list — `resolve_track_credits()` probes each track's `title`/`artist` tags once at build
time (needed because the library ships under `resources/StreamBeats Sync_Use License.pdf`,
Senpai Music Group LLC's Synchronization and Master Use License, whose clause 7 asks for
"reasonable efforts" to credit the author's name + each track's title wherever the music is
used); `build_credits_text()` formats that into `Music:\n- "title" by artist\n...` + one line
naming the license covering the library (never asserted per-track — only what a file's own tags
say); `write_credits()` writes it to the sibling `_MusicCredits.txt` next to Summary/FullMusic/
each Short's output. Shorts additionally store the resolved list on
`Short.segments_used["music"]` (previously just the first track's bare path) so
`GET /projects/{id}` can render a copy-pasteable textarea per short straight from the DB, no
filesystem/ffprobe touch on page render; `music.normalize_music_credits()` coerces the pre-M18
single-string shape so shorts built before this change still show credits. Summary/FullMusic have
no DB-backed track record, so their textarea is read back from the sibling file instead
(`core/projects.read_credits_text()`) — `None` until the next rebuild if one was built before M18.

---

## API Contracts (implemented through M23)

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
`GET /api/thumb/{root}?path=` (jpeg) · `GET /api/download/{root}?path=` (2026-08-20, streams the
file as an attachment via `FileResponse(..., filename=...)`; 404 `ENTITY_NOT_FOUND` if not a file,
400 `PATH_NOT_ALLOWED` on traversal). Login-gated but **not** ownership-scoped for any root —
originally fine when `music` (a shared household resource) was the only browsable root, but the nav
link now defaults to `output_fullflights` so users can download finished videos, and every output
root (`output_fullflights`/`output_summaries`/`output_shorts`/`archive`) is listed as a tab on
`/browse/{root}` via `mount_roots().keys()`. In the current two-user household deployment this means
either user can browse/download the other's finished videos — acceptable for now but a real change
in exposure from the music-only original, worth revisiting if a less-trusted user is ever added.
**M7:** `videos` is no longer a browsable root at all (see Storage & mounts).

**M14: multiselect + delete.** `POST /api/browse/{root}/delete` (form-encoded, repeated `paths=`
+ current `path=`) deletes one or more files and re-renders `partials/listing.html` for that
directory, reporting any per-item failures inline instead of failing the whole request
(`core/filebrowser.delete()` — single-file only, no recursive directory delete). Gated on
`cfg.writable_roots()` (same set M2/M3/M4 already write to), so `music` stays delete-proof exactly
like it stays the one root the app never writes to — the listing UI only renders checkboxes/🗑
buttons for roots in that set. `templates/partials/listing.html` wraps the table in a `<form>`:
each file row gets a `paths` checkbox, a per-row delete button (plain `fetch()` from
`static/browser.js`, not `hx-vals`, so a filename containing a literal `"` can't break JSON
embedded in an HTML attribute), and a toolbar (select-all, "download selected", "delete selected"
gated the same way). "Delete selected" is a normal htmx form post (checked boxes serialize via the
enclosing `<form>`); "download selected" has no server counterpart — `static/browser.js` just
fires the existing single-file `<a download>` pattern once per selected item, staggered ~400ms
apart so browsers don't block/drop near-simultaneous programmatic downloads. Directories never get
a checkbox (no bulk/recursive delete surface at all).

> Flight-log (`/flightlog*`) has been removed — see Core concept above and `features.md` M1a. There
> is no `flightlog` router anymore; those routes 404.

**projects** — `POST /projects/new` (create+redirect; optional `date` form field, defaults to today) ·
`GET /projects/{id}` page (**M16**: calls `core/projects.prune_missing_shorts()` before rendering —
if a `Short.output_file` no longer exists on disk (deleted via the M14 file-browser delete, or by
hand), that row is deleted from the DB and dropped from the list, not just hidden; logged at INFO.
Same on-demand-`Path.exists()` pattern `fullflight_video`/`editor_page` already use for
`preview_file`/`full_flight_file`, just with an actual delete since a `Short` is a disposable
generation record, not a source asset) · `GET /projects/{id}/editor` page ·
flight-type/`flightlog-id`/parts(upload,move,delete)/hike(`upload`,`remove`) mutations ·
**source upload (M11, `core/chunked_upload.py`)** — no longer a single multipart request (a 30-min
Traefik entrypoint `readTimeout` kills any request that takes longer, which any real flight video
does on home upload bandwidth); the client instead splits the file into 8 MiB chunks and calls three
JSON/raw-body endpoints per file: `POST /api/projects/{id}/parts/upload/begin`
(`{filename, total_size}` → `{filename, offset}`, `offset` > 0 means resuming a prior attempt) ·
`PUT /api/projects/{id}/parts/upload/chunk?filename=&offset=` (raw chunk bytes; 409 +
`{"offset": actual}` if the staging file isn't at the expected offset — client resyncs and
continues) · `POST /api/projects/{id}/parts/upload/finish` (`{filename, total_size}` → moves
`VF_UPLOADS_DIR/{project_id}/.staging/{filename}` into place, registers the `SourcePart`). Progress
is entirely server-side-file-size-driven (no session state), so a laptop sleep, network drop, or
even a container restart mid-upload only costs the bytes since the last chunk — re-calling `begin`
picks up from the real on-disk offset. `POST /api/projects/{id}/hike/upload/{begin,chunk,finish}` is
the identical shape at `VF_UPLOADS_DIR/{project_id}/hike/.staging/`, with `finish` additionally
taking `speed_factor` and calling `add_hike_sources`. This is the **only** way to get source footage
in, see Storage & mounts. `POST /api/projects/{id}/hike/remove` (form `index`, removes one
`Hike.sources` entry by position) ·
`GET /api/projects/{id}/fullflight/video` (range stream; serves 720p `preview_file` if
present) · highlight CRUD `GET/POST /api/projects/{id}/highlights[/{hid}[/delete]]` (JSON) ·
**M23:** `POST /api/projects/{id}/highlights/picture-upload` (plain multipart `file=`, not chunked —
images are small; extension checked against `filebrowser.IMAGE_EXT`, stored under
`VF_UPLOADS_DIR/{id}/pictures/`, returns `{"image_path": ...}` to attach on the highlight
create/update call) and `GET /api/projects/{id}/highlights/{hid}/picture` (serves that file back,
ownership-checked, for the editor's preview thumbnail) — registered *before* the
`/highlights/{highlight_id}` routes above, since Starlette matches path patterns before FastAPI
converts params: a literal `picture-upload` segment would otherwise match `{highlight_id}: int`
first and 422 on the failed conversion rather than falling through ·
`GET /api/projects/{id}/flightlog-hints` (M8, `core/flightlog_hints.py`) — segment-derived timeline
hints for the highlight editor, `{"status": "no_launch_marked"|"unavailable"|"ok", "hints": [...]}`;
never errors, always 200 (see Core concept / Storage & mounts style best-effort pattern) ·
builds: `POST /api/projects/{id}/{build,preview/build,summary/build,fullmusic/build,shorts/build}`,
each returning `{"job_id": ...}` **except `shorts/build`**, which since **M24** returns
`{"job_ids": [...]}` — one per short queued in the batch, so the caller (and the Job Queue page)
can see how many videos are actually being produced instead of one opaque job ·
`GET /api/projects/{id}/status`.

**sse** — `GET /events/{job_id}` (EventSource: `{stage,percent,speed,status,result,elapsed_seconds,
queue_position}`) · `GET /api/jobs` · `POST /api/jobs/{id}/cancel` · `GET /jobs` (**M17**: the
Job Queue page — every job for the caller, any project/status, table + cancel button;
`static/jobs.js` polls `GET /api/jobs` every 2s and renders client-side, no HTMX/new backend
surface, same JSON-polling pattern `project.html`'s own build-progress code already uses). All
job-touching routes scoped to the caller's own jobs (`core/jobs.Job.owner_id`, set when
`registry.run()` is called) — a job that exists but belongs to someone else 404s, same as a project.

**Job queue (M13, `core/jobs.py`):** all builds now go through a single global FIFO queue — one
worker thread, so exactly one FFmpeg job runs at a time across the **whole app, every user, every
project** (not per-project or per-user). `registry.run()` used to spawn a dedicated thread per job
that started immediately; it now enqueues and returns, and the job sits `pending` until the one
worker thread reaches it. Rationale: the host has a single GPU encoder and is already
CPU-contended by other unrelated services on `sdh`, so builds racing each other just makes all of
them slower — this lets someone queue up Summary + Full-flight-with-music + Shorts in one go and
walk away, executed one at a time in submission order. `registry.position_in_queue(job_id)` (0 =
running/next-up) is merged into every job payload as `queue_position`. `Job.started_at`/
`finished_at` (unset while queued) back `elapsed_seconds` in `public()` — live elapsed while
running, final duration once terminal — surfaced in the editor's progress UI (`(Nm Ns)` next to
the percentage, "— took Nm Ns" on completion) since a queued job showing bare "pending" at 0% for
up to an hour is exactly the ambiguous state that led to the double-build corruption `v0.4.6`
fixed — don't recreate the failure mode you just fixed. Cancelling a still-*queued* job (`POST
/api/jobs/{id}/cancel`) marks it `cancelled` and the worker skips it without ever invoking its
target — cancelling a *running* one relies on `ffmpeg_runner.encode()` checking `cancel_event` on
every stderr line and `proc.terminate()`-ing, which only fires if ffmpeg is still emitting output;
a true hang with no stderr output would block every job queued behind it until a container
restart, since there is no other escape hatch. `find_active()` (the `v0.4.6` same-kind-same-project
conflict guard) still applies on top of the queue — it stops two identical builds from being
queued back-to-back and redoing the same work, which the FIFO queue alone wouldn't prevent.

**youtube** (M5a, `core/youtube_meta.py`) — `GET /api/projects/{id}/youtube-metadata`: read-only,
`response_model`-typed (visible in `/docs`, unlike the editor's internal JSON endpoints), 404 via the
`07-api-conventions.md` `ENTITY_NOT_FOUND` error shape. Returns project fields + every `Highlight`
(full-flight timestamps, ground truth) + `segment_order` (content order/cumulative duration from
`highlights.merge_overlaps()` over `use_in_summary` highlights — **not** a rendered Summary.mp4
timestamp, see the docstring in `models/youtube.py`) + every `Short` with `segments_used` and its
resolved `source_highlight_name` (null for random-pool shorts). **M19:** also each `Short.credits`
(pasteable music-attribution text, resolved from its own `segments_used["music"]`, no I/O) plus
top-level `summary_credits`/`fullflight_music_credits` (read from the sibling `_MusicCredits.txt`
those two builds write — no DB-backed track list exists for them) — the API surface an external
tool actually needs to paste credits into a video description, same data M18 put on the project
page's UI. No `metadata.json` file is written —
API-only per the live-pull model an external YouTube-management container uses; that container derives
actual chapters/titles/descriptions itself from this raw data. **M6:** also `flight` +
`flight_segments` (`models/flightlog.py`, mirrors Flightlog's contract verbatim) — best-effort;
`None` whenever `external_flight_id`/the owner's API key isn't set or the Flightlog call errors,
logged at INFO and never raised into this endpoint. `POST /api/projects/{id}/flightlog-link`
(body `{youtube_url, label?}`) forwards to Flightlog's `PUT .../links/video/{project_id}` —
called by the external YTChannelMgmt MCP once it actually publishes a video (VidFactory's own
outputs are local files with no public URL, so there's no automatic trigger after a render).

**integration** (M12, `api/routers/integration.py`) — VidFactory's own external contract, mirroring
the shape of Flightlog's `/api/integration/v1` that this app itself calls. API-key authenticated
instead of session-cookie: `Authorization: Bearer <key>` resolved by `require_api_user`
(`api/auth_deps.py`) to a `User` via `users.api_key` (migration `0009`, plaintext — same rationale
as `flightlog_api_key`: a bearer credential presented verbatim, nothing to hash it against on
receipt). Each user generates/regenerates their own key on `/account` (`POST /api/account/api-key`,
`hx-post`+`hx-swap="none"`+`HX-Redirect` like the existing Flightlog-key form — a plain form POST
returning HTML directly would let an F5 on the result page silently mint a new key and invalidate
whatever was already pasted elsewhere). The key stays visible on `/account` on every later visit
(only its own owner can load that page, so persistent-visible is the honest tradeoff given it's
stored plaintext) rather than "shown once" — never returned by any other endpoint. **Per-user, not
a single global key** — a key only ever sees its own user's projects, keeping the M5b ownership
boundary intact for this door too. `require_api_user` logs a warning on every rejected key/missing
header, and `integration.py` logs the calling username on every successful call, since this is the
one surface driven entirely by an external tool with no other visibility into it. Routes, all under
`/api/integration/v1`:
- `GET /projects` — lightweight discovery listing (`project_id`, `date`, `flight_type`,
  `external_flight_id`, `has_full_flight`/`has_summary`/`has_fullflight_music`) for a tool that
  needs to find out what's new rather than being told a project id.
- `GET /projects/{id}/youtube-metadata`, `POST /projects/{id}/flightlog-link` — thin wrappers that
  call the exact same handlers as the session-authed `youtube` router above (only the auth
  dependency differs; zero duplicated logic). Ownership works identically: a project owned by a
  different user 404s `ENTITY_NOT_FOUND`, same as the session-authed routes.
Built for the external YouTube-management repo to pull metadata and push back published-video
links without a browser session. **Not yet deployed or live-verified against that repo.**

---

## Storage & mounts

The share `//172.18.10.10/pg` is **NFS-mounted** on the (now-retired) Fedora host at `/mnt/pg` and
bound into the container at `/data`. **This NAS is unreachable for the next year** (home
network/host changed after a move) — see M7 below. Roots are env-configurable; actual folder names
on the share:

| Env | Value | Use |
|---|---|---|
| `VF_MUSIC` | `/data/music` | music library (read) — **not on the share yet**; standalone deploy bind-mounts a host folder instead (see below) |
| `VF_OUTPUT_SUMMARIES` | `/data/summaries` | summaries + credits (write) |
| `VF_OUTPUT_SHORTS` | `/data/shorts` | shorts (write) |
| `VF_ARCHIVE` | `/data/Archive` | metadata.json + credits (write) |
| `VF_OUTPUT_FULLFLIGHTS` | `/data/fullflights` | concat output + full-flight-with-music + preview (write, wired in M2) |

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
every `v*` tag — `ghcr.io/think4dvantage/vidfactory:latest` and `:vX.Y.Z`, currently `v0.4.3`), and
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
`v0.2.0`–`v0.4.3` so far, each auto-publishing the image on push.

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
- **2026-08-20 recheck: `sdh` is redeployed and current.** `docker inspect` shows image
  `ghcr.io/think4dvantage/vidfactory:0.4.3` (container up ~7h at check time), and
  `curl :8000/health` returns `"encoder":"h264_qsv"` — the M10 fix is live, QSV is actually
  engaging. Confirmed via container logs from a real build (a Summary + full-flight-with-music +
  9 shorts run): every re-encode (preview build, summary, each short) used `-c:v h264_qsv`; the
  initial concat and the full-flight-with-music mux are `-c copy` by design (no re-encode needed)
  so they never touch the encoder — that's not a GPU miss, just a stream-copy step.
- **Verified live this pass**: login-gated builds run end-to-end — Summary, FullFlight+music, and
  all 12 highlight-driven shorts completed successfully on `sdh` — encoder is hardware (`h264_qsv`),
  uploads work. Found this pass: the folder-mode music picker only scanned the immediate contents of
  the selected folder, so any genre organized as subfolders-of-subfolders (every genre here except
  the flat `Ambient`) silently produced music-less output — fixed in `core/music.py`
  (`_scan_durations` now recurses). Root-caused why the shorts build (which the user confirmed did
  have `EDM` selected) showed **no trace at all** in the logs, unlike Summary's build against the
  same folder minutes earlier which logged "Scanned 0 music tracks": `_scan_durations`'s cache-hit
  path (`cache_file.exists()` + matching signature) returned the cached result **before** reaching
  the `logger.info("Scanned ...")` call — so once Summary's build had scanned+cached `EDM` as 0
  tracks, every later request against the same unchanged folder (the shorts build included) hit
  that cache and returned silently, with no log line to show music was ever requested. Fixed by
  moving the log to fire on the cache-hit path too. Combined with the recursion fix above, `EDM`
  will now scan its real tracks instead of caching an empty result in the first place. Also added a
  request-received log line in `build_shorts_ep` (`api/routers/projects.py`) recording the raw
  `music_path` value, as a second line of defense against this class of bug being silent.
  **Still unverified**: a real Flightlog API call.

**2026-08-21 recheck: `sdh` is on `v0.4.8`**, confirmed via `/health` (`"version":"0.4.8"`,
`jobs_queued` present alongside `jobs_active` — that field only exists from M13 on, so its
presence alone confirms the image is current). M9 through M13 are all live: upload fix, GPU fix,
chunked upload, the `FullFlight_withMusic` output-path fix, the folder-recursion + cache-hit music
fixes, the same-kind-same-project build conflict guard, the public API-key integration contract,
and the global FFmpeg job queue. Latest published image: `ghcr.io/think4dvantage/vidfactory:0.4.8`.
See `features.md` "Host migration" roadmap item — still open: a real Flightlog API call, and the
external YouTube-management repo actually calling `/api/integration/v1` (built and locally tested,
never yet exercised by that repo).

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
