from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class HighlightIn(BaseModel):
    name: str
    start: float
    end: float
    comment: str | None = None
    type: str = "video"          # 'video' | 'picture'
    role: str = "normal"         # 'normal' | 'launch' | 'landing'
    image_path: str | None = None
    duration: float | None = None
    use_in_summary: bool = True
    make_short: bool = False


class HighlightOut(HighlightIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
