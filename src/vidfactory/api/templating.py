from __future__ import annotations

from datetime import datetime

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")


def _ts(value: float) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


def _hms(value: float) -> str:
    """Seconds → H:MM:SS (or M:SS under an hour). '0:00' for falsy/None."""
    if not value:
        return "0:00"
    total = int(round(value))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


templates.env.filters["ts"] = _ts
templates.env.filters["hms"] = _hms
