# Frontend Conventions

VidFactory's UI is **server-rendered**: the backend does the work and ships HTML. This fits the actual
functions — a server-side file browser, flight-log tables/forms, multi-step FFmpeg jobs with live
progress, and one interactive screen (the highlight editor). Single operator, so **no i18n and no
in-app auth** (access is gated at the lg4.ch reverse proxy).

## Stack (no build step)

| Concern | Tool | Loaded via |
|---|---|---|
| Templating | Jinja2 (FastAPI `Jinja2Templates`) | server |
| Interactions (forms, tables, navigation, file browser) | **HTMX** | CDN `<script>` |
| Styling | **Tailwind** | CDN `<script>` (Play CDN; no PostCSS/npm) |
| Local interactivity (highlight editor only) | **Alpine.js** + a small vanilla-JS module | CDN + `static/editor.js` |
| Live job progress | **SSE** via `EventSource` | browser native |

**Never introduce npm, a bundler, or a build step.** Everything is a CDN tag or a plain `static/*.js`
module served as-is. Changes to `templates/` and `static/` are live in dev (volume-mounted).

---

## Page Layout Pattern

- One Jinja template per page under `templates/`, extending a shared `base.html` (Tailwind CDN, HTMX,
  nav). Partial templates under `templates/partials/` are returned by HTMX endpoints for in-place swaps.
- Routes that render pages live in the matching domain router (`include_in_schema=False`), returning
  `templates.TemplateResponse(...)`. JSON/data routes in the same router return models.
- HTMX drives most dynamic behaviour: `hx-get`/`hx-post` with `hx-target`/`hx-swap` to replace a
  partial. Prefer returning a rendered partial over hand-writing DOM JS.

---

## Highlight Editor (the one JS-heavy screen)

The editor needs frame-accurate scrubbing and click-to-mark IN/OUT on a timeline — beyond HTMX. Keep
it isolated in `static/editor.js` (an ES module) + Alpine for local state:

- HTML5 `<video>` for playback/scrubbing; keyboard shortcuts (Space play/pause, `I`/`O` set IN/OUT).
- A canvas/SVG timeline drawn over the video duration showing existing highlights (incl. `launch`/
  `landing` roles) and the live IN/OUT selection; click to seek.
- Marks are POSTed to the project router; the highlight list re-renders via an HTMX partial swap.
- All editor state is local to the page; no global SPA framework.

---

## Live Job Progress (SSE)

FFmpeg runs are background jobs. Subscribe with `EventSource` to `/events/{job_id}` and update a
progress bar + stage label from the streamed `{stage, percent, speed, eta}`. Job state is persisted
server-side, so reconnecting after a refresh (or from another device) resumes the live view.

---

## Styling

Use Tailwind utility classes directly in templates. A simple dark palette is fine but not mandated;
keep it consistent via `base.html`. No separate design-token system is required for a single-user tool.

---

## Browser Console Logging Policy

**Log verbosely** in `static/*.js` so frontend behaviour is diagnosable from the console alone.

- Add logging whenever you touch a JS function — not scope creep.
- Prefix every `console.*` with a bracketed module tag derived from the page/file, e.g.
  `[VF:editor]`, `[VF:browser]`, `[VF:jobs]`.

| Event | Level | Include |
|---|---|---|
| Data fetches / HTMX requests | `console.log` | URL, elapsed ms, result summary |
| State transitions (editor marks, job stage) | `console.log` | old → new, payload summary |
| User interactions | `console.log` | action name, params |
| Empty/unexpected results | `console.warn` | expected vs received |
| Errors | `console.error` | full error + context |

For high-frequency loops (e.g. the editor timeline redraw), guard verbose logs behind a throttle.
