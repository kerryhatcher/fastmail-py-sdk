"""Fastmail Python SDK — CalDAV client for calendars and events."""

from fastmail_sdk.caldav.client import CalDavClient
from fastmail_sdk.config import load_credentials
from fastmail_sdk.errors import (
    CalDAVServerError,
    CalendarConflict,
    CalendarNotFound,
    EventConflict,
    EventNotFound,
    FastmailError,
    NotAuthenticated,
)
from fastmail_sdk.models.calendar import Calendar
from fastmail_sdk.models.event import (
    CalendarEvent,
    EventAttendee,
    EventCreate,
    EventQuery,
    EventRecurrence,
    EventReminder,
    EventUpdate,
)

__all__ = [
    "CalDavClient",
    "Calendar",
    "CalendarEvent",
    "CalendarConflict",
    "CalendarNotFound",
    "CalDAVServerError",
    "EventAttendee",
    "EventConflict",
    "EventCreate",
    "EventNotFound",
    "EventQuery",
    "EventRecurrence",
    "EventReminder",
    "EventUpdate",
    "FastmailError",
    "NotAuthenticated",
    "load_credentials",
]
