# Feature History & Backlog

## Current Version: pre-v0.1 (M0–M4 shipped & live at vf-dev.lg4.ch; M5 next)

> Deployed via `scripts/VF-dev.ps1 deploy` (SSH alias `xpsex` → `/opt/VidFactory`). Each milestone
> was verified live on the host. Git branch `m0-foundation` (not pushed to a remote).
> The editor streams a 720p proxy (`preview_file`) for smooth scrubbing.

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| M0a | `.ai/` foundation: dev-web blueprint adopted + filled (VidFactory model, FFmpeg pipeline, mounts, deploy contract). |
| M0b | App skeleton + deploy: FastAPI, `config.py`, SQLite/SQLAlchemy + `db.py` (.sql migrations), ported `ffmpeg_runner`/`gpu_detector`, jobs+SSE, server-side file browser, Dockerfile/compose/`VF-dev.ps1`, GHCR publish workflow. **Deployed live at `vf-dev.lg4.ch`** (HTTP 200, LE cert). `/health` is liveness-based (200 "degraded" when only NAS mounts missing). |
| M1 | Flight log: importer (598 flights + sites from `Flugbuch.xlsx`, frequencies match the audit), outings+sites CRUD, rollups (hours/site-frequency/seasonality), JSON API, Jinja+HTMX UI. **Imported & verified live.** |

| M2 | Concatenate → Full Flight: project-from-outing, ordered source parts (reorder/remove), optional hike prepend (speed_factor; pre-sped = copy), stream-copy concat with SSE progress, NVENC for the sped hike. **Verified live**: 20260502 two 4K parts → `fullflights/20260502_FullFlight.mp4`, duration == sum of parts. |

| M3 | Highlights + Summary: highlight editor (range-streamed full flight, IN/OUT marks, names/roles/flags, timeline), `merge_overlaps` + `auto_fill`, single-pass filter_complex summary (drawtext overlays via textfile, picture highlights, NVENC), music service (folder/file, loudnorm bed, credits), full-flight-with-music (stream-copy + amix). **Verified live**: 20260502 → `Summary.mp4` (60s, overlays+music+credits) and `FullFlight_withMusic.mp4` (6.9GB). |

| M4 | Shorts: highlight-driven (hook→launch?→flying→landing?→CTA, capped <30 s, titled from highlight) + random-pool batch with `UsedMap` de-dup; vertical 1080×1920 normalize (centre-crop), CTA end screen (drawtext), per-short music; NVENC; history in DB. **Verified live**: highlight short 26.0s (hook+launch+3 flying+landing+CTA) and 2 random shorts with no shared flying footage. |
| M1+ | Flight-log polish: search/year/category/glider filters + sortable columns + pagination (`partials/outings_table.html`); stats gained records + by-year + by-category cards; 2-line rows (stats + comment). Managed dropdowns via a `lookups` table (migration 0003, seeded from existing data) editable in a "Manage Dropdown Data" modal; `launch_type` normalised `f/F→forward`, `r→reverse`. xlsx-import button replaced by **CSV export** (`importer` stays a CLI path). **Verified live.** |

### Roadmap (ordered, not yet shipped)

| Milestone | Scope | Exit criteria |
|---|---|---|
| M5 | YouTube artifacts: chapters from highlights, titles/descriptions (Outing-fed), release plan, aggregated credits → `metadata.json` for the MCP | chapters/titles match highlights; MCP consumes the file |

---

## Host setup (Fedora `xpsex`) — needed for a fully green deploy
- **Mount the `pg` SMB share** at `/mnt/pg` (NAS exports only `/volume1/backup` over NFS; `pg` is
  SMB-only, so it needs CIFS credentials). Until mounted, `/data/{InstaOut,Music,Summaries,Shorts}`
  are missing and `/health` reports `degraded` (still HTTP 200; the app stays reachable). `Archive`
  works because the app creates it on local disk.
- **NVIDIA NVENC — host-side fragile in two distinct ways:**
  1. After a **driver update** the CDI spec goes stale; regenerate it:
     `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`.
  2. After a **plain reboot** the `nvidia_uvm` module loads lazily, so `/dev/nvidia-uvm` is missing and
     the container fails to start with `CDI device injection failed … "/dev/nvidia-uvm": no such file or
     directory` — even though `nvidia-smi` works (base modules loaded, UVM not). Fix:
     `sudo modprobe nvidia_uvm && sudo nvidia-modprobe -u -c=0` (then regenerate the CDI spec).
  Dev compose uses the CDI device `nvidia.com/gpu=all`; the app selects `h264_nvenc`.

## Backlog (unordered)

- **Speech-AI auto-highlights** (Whisper + Silero VAD) — port `PS_VidAggregator/SpeechSegmentExtractor.ps1`;
  optional deps. Use case: a 3 h flight where only the spoken segments should be exposed.
- **Remaining Flugbuch sheets** — Groundhandling, Tandemflüge, Fitnessprogramm, Ziele (kept in Excel for now).
- **YTChannelMgmt-side reader** for the `metadata.json` handoff (lands in that repo).
- **Multi-device / job-resume polish** — confirm a running job is visible from a second device after refresh.
- **Linux/Fedora hardening** — reverse-proxy auth (Pocket-ID), backups of the SQLite DB on `Archive/`.
