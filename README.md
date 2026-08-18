# VidFactory

A paragliding **video-production pipeline** for a YouTube channel. A **Project** (one flight,
owned by a logged-in user) assembles source footage into a full flight; a **named Highlight**
marked once on that timeline drives the summary, the shorts, and the YouTube metadata. Flight-log
data (site, glider, IGC analytics) lives in a separate **Flightlog** service this app talks to over
its `/api/integration/v1` API.

> AI/dev context lives in `.ai/` (the single source of truth). This README is derived from it.

## What it does

- **Accounts** — session-cookie login, two users max, no signup UI (see Deploying below).
- **Concatenate** source parts → full flight (sped-up hike prepended for Hike & Fly).
- **Summary** — mark named highlights, auto-fill to a target length, mix background music, write credits.
- **Shorts** — vertical 1080×1920, from named highlights (hook → launch? → flying → landing? → CTA, <30 s)
  or from random pools; batch de-dup so footage is never reused.
- **Flightlog integration** — link a project to a Flightlog flight id; pulls site/glider metadata
  and IGC segment timing (per-user API key, set on `/account`), pushes the finished video's URL back.
- **YouTube metadata API** — `GET /api/projects/{id}/youtube-metadata` exposes highlights (with
  comments), summary content order, shorts (with their source highlight), and Flightlog flight data
  for a separately-run YouTube-management container to pull and turn into chapters/titles/descriptions.

## Stack

Python 3.11 · FastAPI · SQLite/SQLAlchemy · FFmpeg (subprocess, NVENC→QSV→libx264) · Jinja2 + HTMX +
Tailwind. Packaged as a Docker container; deployed to the Fedora host behind Traefik (`vf.lg4.ch`).

## Deploying (from another project)

This repo does not deploy itself — the old Fedora host (`xpsex`) and the home NAS are both
unreachable after a move, and the replacement host is managed elsewhere. This repo's job is to
produce: the image (`.github/workflows/docker-publish.yml` builds + pushes
`ghcr.io/think4dvantage/vidfactory` to GHCR on every `v*` tag), and three files to copy into
whatever project manages the actual deploy host — `docker-compose.standalone.yml`,
`config.yml.example`, `.env.example`. On that host:

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
bind mount. The SQLite DB and uploaded raw video live on local docker volumes (`vf_data`,
`vf_uploads`); music and produced outputs bind-mount host folders instead (WAL mode doesn't work
over NFS, and the NAS is unreachable anyway). `scripts/VF-dev.ps1` and
`docker-compose.yml`/`docker-compose.dev.yml` are the old `xpsex`+Traefik+NAS deploy path — stale
until the new host is provisioned (see `.ai/context/features.md` "Host migration").

## Status

Image published to GHCR; not currently deployed anywhere long-lived — deploy host TBD, see above.
Shipped: concatenate → full flight, highlight editor (720p proxy), summary + full-flight-with-music,
shorts (highlight-driven + random), local video upload, YouTube metadata API, multi-user accounts,
Flightlog API integration. **Next: exact Summary-video chapter timestamps.** See
`.ai/context/features.md` for details and `.ai/` for full context.
