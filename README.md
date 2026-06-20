# VidFactory

Unified paragliding **flight-log + video pipeline** for a YouTube channel. One source of truth: an
**Outing** (one flight) optionally owns a **video project**, and a **named Highlight** marked once on
the assembled full-flight video drives the summary, the shorts, and the YouTube metadata.

> AI/dev context lives in `.ai/` (the single source of truth). This README is derived from it.

## What it does

- **Flight log** — replaces `Flugbuch.xlsx`: outings + sites (elevations) + rollups (hours, site
  frequency, seasonality).
- **Concatenate** source parts → full flight (sped-up hike prepended for Hike & Fly).
- **Summary** — mark named highlights, auto-fill to a target length, mix background music, write credits.
- **Shorts** — vertical 1080×1920, from named highlights (hook → launch? → flying → landing? → CTA, <30 s)
  or from random pools; batch de-dup so footage is never reused.
- **YouTube artifacts** — chapters (from highlights), titles/descriptions, release plan, music credits →
  `metadata.json` consumed by the YTChannelMgmt MCP.

## Stack

Python 3.11 · FastAPI · SQLite/SQLAlchemy · FFmpeg (subprocess, NVENC→QSV→libx264) · Jinja2 + HTMX +
Tailwind. Packaged as a Docker container; deployed to the Fedora host behind Traefik (`vf.lg4.ch`).

## Run (dev, on the Fedora Docker host)

```bash
cp config.yml.example config.yml      # adjust mount roots if needed
# from a Windows workstation, deploy to the host over SSH:
pwsh ./scripts/VF-dev.ps1 deploy      # → https://vf-dev.lg4.ch
```

The container expects the NAS share `//172.18.10.10/pg` **NFS-mounted** at `/mnt/pg` → `/data`
(folders `InstaOut`, `music`, `fullflights`, `summaries`, `shorts`, `Archive`). The SQLite DB lives on
a **local** docker volume (`vf_data`), not the NAS (WAL doesn't work over NFS).

## Status

Live at **https://vf-dev.lg4.ch**. Shipped: flight log (Flugbuch import), concatenate → full flight,
highlight editor (720p proxy), summary + full-flight-with-music, shorts (highlight-driven + random).
**Next: M5 — YouTube metadata.** See `.ai/context/features.md` for details and `.ai/` for full context.
