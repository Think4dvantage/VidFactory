# API Conventions

## Philosophy

Standardizing API responses ensures that the frontend can handle both successes and errors in a predictable way. All API endpoints must follow these conventions for a consistent developer and user experience.

---

## Success Response

For single entities, return the object directly. For collections, return a list wrapped in a `data` key.

### Single Entity
```json
{
  "id": "123",
  "name": "Widget A",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### Collection
```json
{
  "data": [
    { "id": "123", "name": "Widget A" },
    { "id": "456", "name": "Widget B" }
  ],
  "total": 2
}
```

---

## Error Response (Problem Details)

When an error occurs, the API must return a standardized error object. This is loosely based on [RFC 7807](https://datatracker.ietf.org/doc/html/rfc7807).

### Format
```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message explaining the error.",
    "details": { "any": "extra context" }
  }
}
```

### Common Error Codes
- `ENTITY_NOT_FOUND`: Resource with the given ID does not exist.
- `VALIDATION_FAILED`: Request payload is invalid (Pydantic error).
- `CONFLICT`: Resource already exists or version mismatch.
- `PATH_NOT_ALLOWED`: A file-browser path escaped the configured mount roots.
- `MOUNT_UNAVAILABLE`: A required NAS mount root is missing or not writable.
- `FFMPEG_FAILED`: An encode/probe subprocess exited non-zero (include the failing command in `details`).
- `INTERNAL_ERROR`: Unexpected server-side failure.

### Example: Validation Error
```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Field 'email' must be a valid email address.",
    "details": { "field": "email", "value": "invalid-email" }
  }
}
```

---

## HTTP Status Codes

| Code | Use Case |
|---|---|
| **200 OK** | Successful read or update. |
| **201 Created** | Successful resource creation. |
| **400 Bad Request** | Validation failed or bad logic (e.g., disallowed path, negative duration). |
| **404 Not Found** | Resource ID is invalid or missing. |
| **409 Conflict** | Resource with this key already exists. |
| **500 Internal Error** | Database lock, FFmpeg failure, logic bug, or unexpected exception. |
| **503 Service Unavailable** | A required NAS mount is unavailable (see `/health`). |

---

## Implementation (FastAPI)

Use a global exception handler to catch common errors and return the standardized JSON format.

```python
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details
            }
        }
    )
```
