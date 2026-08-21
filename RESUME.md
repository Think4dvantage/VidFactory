# Resume Notes — 2026-08-21

## In Progress

Nothing mid-implementation — the last several sessions each shipped a complete, tagged,
tested, deployed fix/feature (`v0.4.4` through `v0.4.8`). `sdh` is confirmed live on `v0.4.8`
via `/health`. Nothing is half-done in the working tree.

## Next Step

Two real end-to-end verifications are still outstanding, neither blocked on this repo:

1. **Connect the external YouTube-management repo to `/api/integration/v1`.** Built and locally
   tested (`tests/backend/test_integration_api.py`), but no real external client has ever called
   it. Generate a key on `/account`, point that other repo's config at
   `https://vf.lenti.cloud/api/integration/v1` with `Authorization: Bearer <key>`, and confirm
   `GET /projects` and `GET /projects/{id}/youtube-metadata` return real data.
2. **A real Flightlog API call has never been exercised** — `core/flightlog_client.py` was only
   ever verified against `httpx.MockTransport`. Set a Flightlog API key on `/account`, link a
   project's `external_flight_id`, and confirm `flight`/`flight_segments` populate on the
   `youtube-metadata` response instead of staying `null`.

## Open Questions / Known Gaps (not bugs, just unfinished)

- **M5 remainder**: Summary chapter timestamps in `youtube-metadata` are content-order only, not
  exact rendered-video timestamps (`auto_fill` filler shifts real offsets, and the per-build
  target length isn't persisted). Would need `target_seconds` persisted on the `Project`/`Short`
  row to fix properly.
- **Traefik `responseHeaderTimeout` fix** for the 60s upload timeout is identified but lives in a
  separate infra repo (`/opt/sdh.lol/compose/infrastructure/traefik/config/dynamic.yml`) — on the
  user to apply, not something this repo can do. M11's chunked upload works around it client-side
  regardless, so this is a nice-to-have, not a blocker.
- **Job queue caveat**: cancelling a *running* job relies on `ffmpeg_runner.encode()` seeing
  `cancel_event` on the next stderr line. A job that truly hangs with zero ffmpeg output has no
  escape hatch except a container restart — and since M13, that now blocks every job queued
  behind it too, not just itself. Hasn't happened yet; worth knowing if a build ever seems
  permanently stuck with 0% CPU (check `ps aux`/`docker stats` on `sdh` before assuming a code bug
  — see the M13 session's diagnostic pattern in `.ai/context/architecture.md`'s Deployment
  section).
- **`sdh` is shared with several unrelated services** (Flightlog, Jellyfin-adjacent stuff,
  SABnzbd, `lenticularis`, `lsmfapi`) and runs at a load average around 10-12 much of the time.
  Heavy-filter builds (many highlights with `drawtext` overlays) are CPU-bound, not GPU-bound —
  only the final encode uses QSV — so a Summary with ~19 segments realistically takes 50-60
  minutes. This is expected, not a regression, unless it's meaningfully longer than that.

## Context worth knowing that isn't obvious from the code alone

- **`sdh` replaced the old `xpsex`/`lg4.ch` homelab host** after a 2026 move; this is a
  user-wide infra fact, not VidFactory-specific — already corrected in
  `.ai/instructions/05-user-profile.md` and `.ai/context/architecture.md`'s Deployment section.
- **Direct `ssh sdh`/`docker exec` access was used this session** to diagnose live issues (stuck
  builds, corrupted output, log inspection) and, once, to move a misplaced output file + fix its
  DB row after explicit user approval. `04-constraints.md` says "never touch prod directly, no
  direct SSH" — this was investigative/diagnostic access with the user present and approving each
  step, not an unattended production change path. Future sessions should still default to
  tag-and-push, and treat direct host access as an exception for live debugging, not a routine
  deploy mechanism.
- **Every version bump needs two files in sync**: `pyproject.toml`'s `version` *and*
  `src/vidfactory/__init__.py`'s `__version__` are independent strings — missing one (as happened
  once this session, `v0.4.4`) makes `/health` report a stale version even on a freshly deployed
  image.
