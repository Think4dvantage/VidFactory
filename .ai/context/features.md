# Feature History & Backlog

## Current Version: pre-v0.1 (in progress)

### Shipped Milestones

| Milestone | What shipped |
|---|---|
| M0a | `.ai/` foundation: dev-web blueprint adopted; `01-project-overview.md`, `context/architecture.md`, and this file filled with the real VidFactory model, FFmpeg pipeline, mounts, and deploy contract. (No application code yet — gated for user review.) |

### Roadmap (ordered, not yet shipped)

| Milestone | Scope | Exit criteria |
|---|---|---|
| M0b | Skeleton & deploy: FastAPI app, `config.py`, SQLite/SQLAlchemy + `db.py`, server-side file browser, ported `ffmpeg_runner.py`+`gpu_detector.py`, `Dockerfile`/compose/`VF-dev.ps1`; register in lg4.ch; add `pg` CIFS mount | `vf-dev.lg4.ch` serves, `/health` green, browses `/data` |
| M1 | Flight log: outings + sites CRUD + rollups (hours/site-frequency/seasonality); import 598 flights + 27/27 sites from `Flugbuch.xlsx` | importer reproduces audit numbers (Hohwald 110, Höhenmatte 306) |
| M2 | Concatenate → Full Flight: ordered parts + sped-up hike prepend (H&F), SSE progress | `DATE_FullFlight.mp4` plays, correct order |
| M3 | Highlights + Summary: highlight editor (player + IN/OUT marks, names, launch/landing roles), dedup, auto-fill, summary + music + credits, full-flight-with-music | `DATE_Summary.mp4` + `DATE_FullFlight_withMusic.mp4` + credits |
| M4 | Shorts: highlight-driven (hook→launch?→flying→landing?→CTA, <30 s) + random-pool with `UsedMap`; vertical normalize; music | shorts render; batch has no footage reuse |
| M5 | YouTube artifacts: chapters from highlights, titles/descriptions (Outing-fed), release plan, aggregated credits → `metadata.json` for the MCP | chapters/titles match highlights; MCP consumes the file |

---

## Backlog (unordered)

- **Speech-AI auto-highlights** (Whisper + Silero VAD) — port `PS_VidAggregator/SpeechSegmentExtractor.ps1`;
  optional deps. Use case: a 3 h flight where only the spoken segments should be exposed.
- **Remaining Flugbuch sheets** — Groundhandling, Tandemflüge, Fitnessprogramm, Ziele (kept in Excel for now).
- **YTChannelMgmt-side reader** for the `metadata.json` handoff (lands in that repo).
- **Multi-device / job-resume polish** — confirm a running job is visible from a second device after refresh.
- **Linux/Fedora hardening** — reverse-proxy auth (Pocket-ID), backups of the SQLite DB on `Archive/`.
