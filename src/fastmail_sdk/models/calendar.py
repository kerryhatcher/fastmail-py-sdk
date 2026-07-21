"""Calendar model — a CalDAV calendar collection."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Calendar(BaseModel):
    """A Fastmail calendar (CalDAV collection)."""

    id: str = Field(description="Resource ID derived from the CalDAV href")
    name: str = Field(description="Display name")
    color: str | None = Field(default=None, description="Hex color, e.g. '#3a87ad'")
    description: str | None = Field(default=None, description="Calendar description")
    href: str = Field(description="CalDAV resource path, e.g. '/dav/calendars/user/.../Default/'")
    etag: str | None = Field(default=None, description="Current ETag for optimistic concurrency")
    ctag: str | None = Field(default=None, description="Calendar collection tag (change detection)")
    is_default: bool = Field(default=False, description="Primary/default calendar")
