# Feature History & Backlog

## Current Version: pre-v0.1 (in progress)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| M0a | `.ai/` foundation: dev-web blueprint adopted + filled (VidFactory model, FFmpeg pipeline, mounts, deploy contract). |
| M0b | App skeleton + deploy: FastAPI, `config.py`, SQLite/SQLAlchemy + `db.py` (.sql migrations), ported `ffmpeg_runner`/`gpu_detector`, jobs+SSE, server-side file browser, Dockerfile/compose/`VF-dev.ps1`, GHCR publish workflow. **Deployed live at `vf-dev.lg4.ch`** (HTTP 200, LE cert). `/health` is liveness-based (200 "degraded" when only NAS mounts missing). |
| M1 | Flight log: importer (598 flights + sites from `Flugbuch.xlsx`, frequencies match the audit), outings+sites CRUD, rollups (hours/site-frequency/seasonality), JSON API, Jinja+HTMX UI. **Imported & verified live.** |

### Roadmap (ordered, not yet shipped)

| Milestone | Scope | Exit criteria |
|---|---|---|
| M2 | Concatenate → Full Flight: ordered parts + sped-up hike prepend (H&F), SSE progress | `DATE_FullFlight.mp4` plays, correct order |
| M3 | Highlights + Summary: highlight editor (player + IN/OUT marks, names, launch/landing roles), dedup, auto-fill, summary + music + credits, full-flight-with-music | `DATE_Summary.mp4` + `DATE_FullFlight_withMusic.mp4` + credits |
| M4 | Shorts: highlight-driven (hook→launch?→flying→landing?→CTA, <30 s) + random-pool with `UsedMap`; vertical normalize; music | shorts render; batch has no footage reuse |
| M5 | YouTube artifacts: chapters from highlights, titles/descriptions (Outing-fed), release plan, aggregated credits → `metadata.json` for the MCP | chapters/titles match highlights; MCP consumes the file |

---

## Host setup (Fedora `xpsex`) — needed for a fully green deploy
- **Mount the `pg` SMB share** at `/mnt/pg` (NAS exports only `/volume1/backup` over NFS; `pg` is
  SMB-only, so it needs CIFS credentials). Until mounted, `/data/{InstaOut,Music,Summaries,Shorts}`
  are missing and `/health` reports `degraded` (still HTTP 200; the app stays reachable). `Archive`
  works because the app creates it on local disk.
- **Repair the NVIDIA Container Toolkit** — host ldcache references a missing `libEGL_nvidia.so.<ver>`,
  so the NVENC `deploy.resources` block in `docker-compose.dev.yml` is commented out (QSV/libx264 used
  meanwhile). Fix on host, then uncomment to enable NVENC.

## Backlog (unordered)

- **Speech-AI auto-highlights** (Whisper + Silero VAD) — port `PS_VidAggregator/SpeechSegmentExtractor.ps1`;
  optional deps. Use case: a 3 h flight where only the spoken segments should be exposed.
- **Remaining Flugbuch sheets** — Groundhandling, Tandemflüge, Fitnessprogramm, Ziele (kept in Excel for now).
- **YTChannelMgmt-side reader** for the `metadata.json` handoff (lands in that repo).
- **Multi-device / job-resume polish** — confirm a running job is visible from a second device after refresh.
- **Linux/Fedora hardening** — reverse-proxy auth (Pocket-ID), backups of the SQLite DB on `Archive/`.
