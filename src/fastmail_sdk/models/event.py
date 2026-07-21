"""Calendar event models — the core data types for CalDAV events."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EventDateTime(BaseModel):
    """A date/time value for an event start or end.

    ``value`` is an ISO 8601 string: ``YYYY-MM-DD`` for all-day events,
    ``YYYY-MM-DDTHH:MM:SS`` for floating local time, or
    ``YYYY-MM-DDTHH:MM:SSZ`` for UTC.
    """

    value: str
    timezone: str | None = Field(default=None, description="IANA timezone, e.g. 'America/New_York'")
    all_day: bool = Field(default=False)


class EventAttendee(BaseModel):
    """An event participant."""

    email: str
    name: str | None = None
    role: str | None = Field(
        default=None,
        description="CHAIR, REQ-PARTICIPANT, OPT-PARTICIPANT, or NON-PARTICIPANT",
    )
    partstat: str | None = Field(
        default=None,
        description="NEEDS-ACTION, ACCEPTED, DECLINED, TENTATIVE, DELEGATED, COMPLETED, or IN-PROCESS",
    )
    rsvp: bool | None = None


class EventRecurrence(BaseModel):
    """Recurrence rule (RRULE)."""

    frequency: str = Field(description="SECONDLY, MINUTELY, HOURLY, DAILY, WEEKLY, MONTHLY, or YEARLY")
    interval: int | None = Field(default=None, ge=1)
    count: int | None = Field(default=None, ge=1)
    until: str | None = Field(default=None, description="YYYYMMDD or YYYYMMDDTHHMMSSZ")
    by_day: list[str] = Field(default_factory=list, description="e.g. ['MO', 'WE', 'FR'] or ['-1SU']")


class EventReminder(BaseModel):
    """Alarm/reminder for an event (VALARM)."""

    minutes_before: int
    action: str = Field(default="DISPLAY", description="DISPLAY, AUDIO, EMAIL, or PROCEDURE")


class CalendarEvent(BaseModel):
    """A calendar event (VEVENT) with its CalDAV metadata."""

    id: str = Field(description="UID — unique across all calendars")
    calendar_id: str = Field(description="Resource ID of the parent calendar")
    calendar_name: str | None = Field(default=None)
    href: str | None = Field(default=None, description="CalDAV resource path, e.g. '/dav/.../uid.ics'")
    etag: str | None = Field(default=None, description="Current ETag for optimistic concurrency")
    title: str
    start: EventDateTime
    end: EventDateTime
    location: str | None = None
    description: str | None = None
    attendees: list[EventAttendee] = Field(default_factory=list)
    recurrence: EventRecurrence | None = None
    reminders: list[EventReminder] = Field(default_factory=list)


class EventQuery(BaseModel):
    """Filter criteria for listing events."""

    calendar_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None


class EventCreate(BaseModel):
    """Input for creating a new event."""

    calendar_id: str | None = Field(default=None, description="Target calendar; defaults to the primary calendar")
    title: str
    start: str = Field(description="YYYY-MM-DD, YYYY-MM-DDTHH:MM[:SS], or RFC 3339")
    end: str = Field(description="YYYY-MM-DD, YYYY-MM-DDTHH:MM[:SS], or RFC 3339")
    timezone: str | None = Field(default=None, description="IANA timezone for naive local datetimes")
    location: str | None = None
    description: str | None = None
    attendees: list[str] = Field(default_factory=list, description="Email addresses")
    recurrence_freq: str | None = Field(default=None, description="DAILY, WEEKLY, MONTHLY, etc.")
    recurrence_interval: int | None = Field(default=None, ge=1)
    recurrence_count: int | None = Field(default=None, ge=1)
    recurrence_until: str | None = None
    recurrence_by_day: list[str] = Field(default_factory=list)
    reminder_minutes: list[int] = Field(default_factory=list, description="Minutes before event")


class EventUpdate(BaseModel):
    """Input for updating an existing event. All fields optional — only provided fields change."""

    title: str | None = None
    start: str | None = None
    end: str | None = None
    timezone: str | None = None
    location: str | None = None
    description: str | None = None
    attendees: list[str] | None = Field(default=None, description="Replace attendees with this set")
    clear_attendees: bool = False
    recurrence_freq: str | None = None
    recurrence_interval: int | None = None
    recurrence_count: int | None = None
    recurrence_until: str | None = None
    recurrence_by_day: list[str] | None = None
    clear_recurrence: bool = False
    reminder_minutes: list[int] | None = None
    clear_reminders: bool = False
