# VidFactory

A paragliding **video-production pipeline** for a YouTube channel. A **Project** (one flight,
owned by a logged-in user) assembles uploaded source footage into a full flight; a **named
Highlight** marked once on that timeline drives the summary, the shorts, and the YouTube metadata.
Flight-log data (site, glider, IGC analytics) lives in a separate **Flightlog** service this app
talks to over *its* `/api/integration/v1` contract — not to be confused with VidFactory's own
same-named contract described below, which an external YouTube-management tool calls *into* this
app.

> AI/dev context lives in `.ai/` (the single source of truth). This README is derived from it.

## What it does

- **Accounts** — session-cookie login, two users max, no signup UI (see Deploying below).
- **Upload** source footage — chunked, resumable (8 MiB chunks; survives a laptop sleep, a network
  drop, even a container restart mid-upload). The only way in: there's no NAS browsing any more,
  the home NAS this app originally read from has been unreachable since a 2026 move.
- **Concatenate** source parts → full flight (sped-up hike prepended for Hike & Fly); a 720p proxy
  is built alongside it for smooth scrubbing in the highlight editor.
- **Summary** — mark named highlights, auto-fill to a target length, mix background music, write
  credits. A separate action mixes music straight onto the full flight instead.
- **Shorts** — vertical 1080×1920, from named highlights (hook → launch? → flying → landing? → CTA,
  <30 s) or from random pools; batch de-dup so footage is never reused.
- **One build queue** — every render (concat, preview, summary, full-flight+music, shorts) goes
  through a single FIFO queue, one job at a time. Queue up several builds and walk away; the
  progress UI shows queue position and elapsed time so a still-waiting job never looks stuck.
- **Flightlog integration** — link a project to a Flightlog flight id; pulls site/glider metadata
  and IGC segment timing (per-user API key, set on `/account`), pushes the finished video's URL
  back once published. Flightlog-derived timeline hints (thermal starts, etc.) also overlay on the
  highlight editor once a launch highlight is marked.
- **A file browser with downloads** — browse the music library and every output root
  (full flights, summaries, shorts, archive), with a download button per file.
- **Two ways for an external tool to read project data**:
  - `GET /api/projects/{id}/youtube-metadata` — session-cookie authenticated, for this app's own
    editor/tooling.
  - `/api/integration/v1` — API-key authenticated (`Authorization: Bearer <key>`, generated on
    `/account`), for a separately-run YouTube-management tool to pull metadata and push back a
    published-video link without ever logging in. Per-user scoped — a key only ever sees its own
    user's projects.

## Stack

Python 3.11 · FastAPI · SQLite/SQLAlchemy · FFmpeg (subprocess, NVENC→QSV→libx264, currently
running Intel QSV) · Jinja2 + HTMX + Tailwind. Packaged as a Docker container.

## Deploying (from another project)

This repo does not deploy itself. It produces the image
(`.github/workflows/docker-publish.yml` builds + pushes `ghcr.io/think4dvantage/vidfactory` to
GHCR on every `v*` tag) and three files to copy into whatever project manages the actual deploy
host — `docker-compose.standalone.yml`, `config.yml.example`, `.env.example`. It currently runs on
a shared homelab host (SSH alias `sdh`, domain `vf.lenti.cloud`), managed from a separate
Docker-host/IaC repo, not this one. On that host:

```bash
cp config.yml.example config.yml      # adjust mount roots if needed
cp .env.example .env                  # set VF_MUSIC_HOST/VF_LIBRARY_HOST + VF_BOOTSTRAP_USERNAME/
                                       # _PASSWORD (first login) + VF_FLIGHTLOG_URL (optional)
docker compose -f docker-compose.standalone.yml up -d
curl http://localhost:8000/health
```

No signup UI: `VF_BOOTSTRAP_USERNAME`/`VF_BOOTSTRAP_PASSWORD` create the first account on first
boot; add a second user afterwards with `docker exec -it <container> python scripts/create_user.py
<username>`. `docker-compose.standalone.yml` publishes `8000:8000` directly — no Traefik, no NAS
bind mount. The SQLite DB lives on a local docker volume (`vf_data`); uploaded raw video, music,
and produced outputs are bind-mounted host folders (WAL mode doesn't work over NFS, hence the local
volume for the DB specifically). `scripts/VF-dev.ps1` and `docker-compose.yml`/
`docker-compose.dev.yml` target the old, now-dead `xpsex` host — stale, not part of the current
deploy path.

## Status

Live and healthy on `sdh` (`vf.lenti.cloud`). Shipped: concatenate → full flight, highlight editor
(720p proxy + Flightlog timeline hints), summary + full-flight-with-music, shorts (highlight-driven
+ random), chunked resumable upload, a file browser with downloads, multi-user accounts, Flightlog
API integration, a public API-key integration contract for external tools, and a global build
queue with live progress. **Next:** exact Summary-video chapter timestamps, and getting the
external YouTube-management tool actually talking to `/api/integration/v1`. See
`.ai/context/features.md` for the full milestone history and `.ai/` for full context.
