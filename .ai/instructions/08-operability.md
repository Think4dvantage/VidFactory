# Operability Conventions

Operability is goal #1. Every service must be fully diagnosable from its logs alone — no source-diving, no attaching debuggers. An operator who has never read the code should be able to understand exactly what the service is doing, why, and whether it is healthy.

---

## The Rule

**Log everything that matters. When in doubt, log it.**

Logging is never scope creep. Any time you add or modify a function — even for an unrelated fix — check whether it has logging. If it does not, add it before moving on.

---

## Backend / Service Logging

Use Python's standard `logging` module throughout. Never use `print()` in production code.

### Logger Setup

Each module gets its own named logger:

```python
import logging
logger = logging.getLogger(__name__)
```

Configure the root logger at startup in `main.py` lifespan with level, format, and handler. Log format must include timestamp, level, logger name, and message:

```
2026-06-20 14:32:01,234 INFO     [vidfactory.api.main] Service starting — version 0.1.0
2026-06-20 14:32:01,891 INFO     [vidfactory.database.db] Migrations applied: 3 (skipped: 1)
```

### Startup Sequence

Log every step of the startup sequence at `INFO`. An operator reading cold logs must be able to reconstruct exactly what the service did during boot:

| Event | What to log |
|---|---|
| Service start | Name, version, environment (dev/prod) |
| Config loaded | Source file path, key values (never secrets) — e.g. log level, DB path, mount roots |
| DB initialised | WAL mode status, number of migrations applied vs skipped |
| Mounts checked | Each root (`VF_MUSIC`/`VF_OUTPUT_*`/`VF_ARCHIVE` — no `VF_VIDEOS` since M7, NAS video browsing was removed): path + readable/writable |
| GPU detected | Selected encoder (h264_nvenc / h264_qsv / libx264) and why |
| HTTP server ready | Bind address and port |

### Request Lifecycle

Every API request must produce at least one log line. Use FastAPI middleware or a dependency to log at `INFO`:

```
INFO  [vidfactory.api.middleware] POST /api/concat → 202 (8ms) job=ab12
INFO  [vidfactory.api.middleware] GET  /api/flightlog/outings → 200 (12ms) count=598
```

Log at `WARNING` for 4xx, `ERROR` for 5xx. Include request ID if available.

### FFmpeg / Background Jobs

Every job execution must be bracketed with start and end log lines, and the **full FFmpeg command**
must be logged before it runs (operators reproduce failures by copy-pasting it):

```python
logger.info("Job [summary] starting — project=%s target=%ss", project_id, target_len)
logger.info("Job [summary] ffmpeg: %s", " ".join(args))
# ... run with progress callbacks ...
logger.info("Job [summary] done — %s in %.1fs", output_file, elapsed)
```

Log at `WARNING` for recoverable anomalies (e.g. music folder empty → no music). Log at `ERROR` with
the failing command and the last ~30 stderr lines if FFmpeg exits non-zero — never swallow it.

### Database Operations

Log at `DEBUG` for individual queries in hot paths (use `DEBUG` so they can be silenced in prod). Log at `INFO` for migrations, bulk writes, schema changes. Log at `ERROR` for constraint violations or connection failures with full context.

### Log Levels — When to Use Each

| Level | When |
|---|---|
| `DEBUG` | Per-row/per-iteration detail, query parameters, cache internals |
| `INFO` | All significant state changes: startup steps, job runs, API requests, config values |
| `WARNING` | Recoverable anomalies: empty results, retries, degraded mode, deprecated usage |
| `ERROR` | Failures that affect correctness: exceptions, failed writes, auth errors |
| `CRITICAL` | Service cannot continue: DB unreachable at startup, config missing required field |

---

## Health Endpoint

Every service must expose a `GET /health` endpoint. It must:

- Return `200 OK` with a JSON body when all critical subsystems are up
- Return `503 Service Unavailable` with a descriptive JSON body when any critical subsystem is down
- Be callable without authentication
- Include at minimum: service name, version, uptime, DB status, mount status, active job count

```json
{
  "status": "ok",
  "service": "vidfactory",
  "version": "0.1.0",
  "uptime_seconds": 3621,
  "checks": {
    "sqlite": "ok",
    "mounts": { "music": "ok", "output_fullflights": "ok", "output_summaries": "ok", "output_shorts": "ok", "archive": "ok" },
    "encoder": "h264_nvenc",
    "jobs_active": 1
  }
}
```

Log every health check that returns non-ok at `WARNING`.

---

## Config Transparency

Config must be explicit and auditable:

- All tunable behaviour (intervals, timeouts, thresholds, feature flags) must be in `config.yml`, not hardcoded.
- On startup, log the resolved value of every non-secret config key at `INFO`.
- Provide a `config.yml.example` with every key documented and a sensible default.
- If a required key is missing, log at `CRITICAL` with the key name and fail fast — never silently fall back to a magic default.

---

## Structured Error Context

When logging an exception, always include context that tells the operator what the service was trying to do:

```python
# Good
logger.error("Job [summary] ffmpeg failed — exit=%d cmd=%s stderr=%.500s",
             code, cmd, stderr_tail, exc_info=True)

# Bad
logger.error("Parse error", exc_info=True)
```

Never catch-and-ignore exceptions. If an exception cannot be re-raised, log it at `ERROR` with full context.

---

## Frontend

See `03-frontend-conventions.md` — Browser Console Logging Policy. The same philosophy applies: log everything at the browser console so any engineer can diagnose frontend behaviour without source-diving. Use the bracketed prefix convention (`[VF:<page>]`) on every `console.*` call.
