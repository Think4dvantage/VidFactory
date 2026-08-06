from __future__ import annotations

from datetime import date as Date_, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Project(Base):
    """The video work for a flight. Standalone — flight-log metadata (site/glider/date) lives in a
    separate Flightlog service; `external_flight_id` is an opaque, unenforced future reference to
    it (see `core/flightlog_client.py`)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[Date_ | None] = mapped_column(Date)
    external_flight_id: Mapped[int | None] = mapped_column(Integer)
    flight_type: Mapped[str] = mapped_column(String, default="normal_flight")  # | 'hike_and_fly'
    full_flight_file: Mapped[str | None] = mapped_column(String)
    preview_file: Mapped[str | None] = mapped_column(String)  # 720p editor proxy
    summary_file: Mapped[str | None] = mapped_column(String)
    fullflight_music_file: Mapped[str | None] = mapped_column(String)
    youtube_metadata_file: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    source_parts: Mapped[list["SourcePart"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    hike: Mapped["Hike | None"] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    highlights: Mapped[list["Highlight"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    pools: Mapped[list["Pool"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    shorts: Mapped[list["Short"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class SourcePart(Base):
    __tablename__ = "source_parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    file: Mapped[str] = mapped_column(String, nullable=False)
    order: Mapped[int] = mapped_column("part_order", Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="source_parts")


class Hike(Base):
    """Hiking footage that is sped up and prepended to the full flight (Hike & Fly)."""

    __tablename__ = "hikes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), unique=True, nullable=False)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    speed_factor: Mapped[float] = mapped_column(Float, default=32.0)

    project: Mapped[Project] = relationship(back_populates="hike")


class Highlight(Base):
    """The shared spine: a named mark on the full-flight timeline."""

    __tablename__ = "highlights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    start: Mapped[float] = mapped_column("start_s", Float, nullable=False)
    end: Mapped[float] = mapped_column("end_s", Float, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String, default="video")  # 'video' | 'picture'
    role: Mapped[str] = mapped_column(String, default="normal")  # 'normal' | 'launch' | 'landing'
    image_path: Mapped[str | None] = mapped_column(String)
    duration: Mapped[float | None] = mapped_column(Float)
    use_in_summary: Mapped[bool] = mapped_column(Boolean, default=True)
    make_short: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="highlights")


class Pool(Base):
    """Range on the full flight used for random short sampling (legacy mode)."""

    __tablename__ = "pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # 'flying' | 'hiking'
    start: Mapped[float] = mapped_column("start_s", Float, nullable=False)
    end: Mapped[float] = mapped_column("end_s", Float, nullable=False)

    project: Mapped[Project] = relationship(back_populates="pools")


class Short(Base):
    __tablename__ = "shorts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    output_file: Mapped[str] = mapped_column(String, nullable=False)
    short_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    duration: Mapped[float | None] = mapped_column(Float)
    segments_used: Mapped[dict] = mapped_column(JSON, default=dict)
    source_highlight_id: Mapped[int | None] = mapped_column(ForeignKey("highlights.id"))
    title: Mapped[str | None] = mapped_column(String)

    project: Mapped[Project] = relationship(back_populates="shorts")
