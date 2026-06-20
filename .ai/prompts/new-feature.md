# Prompt: Implement a New Feature

Use this prompt as a checklist when implementing any non-trivial feature end-to-end.

---

## Backend Checklist

- [ ] New SQLite table(s) → add ORM model in `database/models.py`, add a sequential `.sql` migration in `database/migrations/` (see `02-backend-conventions.md`)
- [ ] New Pydantic schemas in `src/vidfactory/models/`
- [ ] New router at `src/vidfactory/api/routers/{domain}.py` → register in `main.py`
- [ ] Long FFmpeg/ffprobe work → run via `core/jobs.py` + `core/ffmpeg_runner.py`, stream progress over SSE (never block the request)
- [ ] New filesystem access stays within the configured mount roots (use `core/filebrowser.py` path guard)
- [ ] Config keys for anything configurable → add to `config.py` Pydantic models AND `config.yml.example`

## Frontend Checklist

- [ ] New Jinja template under `templates/` (extends `base.html`); HTMX partials under `templates/partials/`
- [ ] Page route in the matching domain router (`include_in_schema=False`) returning `TemplateResponse`
- [ ] Dynamic updates via HTMX (`hx-get`/`hx-post` + target/swap) returning a rendered partial — avoid hand-written DOM JS
- [ ] Editor-grade interactivity (video scrub / IN-OUT marking) only → isolate in `static/editor.js` + Alpine
- [ ] Long-job screens subscribe to `/events/{job_id}` via `EventSource` for live progress
- [ ] Console logging added to every new/modified JS function, `[VF:<page>]` prefix (see frontend conventions)
- [ ] No npm / bundler / build step (CDN tags only)

## Quality

- [ ] No hardcoded config values — all through `get_config()`
- [ ] No print statements — use `logging`
- [ ] Type hints on all function signatures
- [ ] No npm / build step introduced

Refer to `.ai/context/architecture.md` for data models and API contracts.
Refer to `.ai/context/features.md` for backlog context on what's planned.
