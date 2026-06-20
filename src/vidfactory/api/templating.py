from __future__ import annotations

from datetime import datetime

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")


def _ts(value: float) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


templates.env.filters["ts"] = _ts
