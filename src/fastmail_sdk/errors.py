"""Error types for the Fastmail SDK."""

from __future__ import annotations


class FastmailError(Exception):
    """Base exception for all SDK errors."""


class NotAuthenticated(FastmailError):
    """Credentials are missing or invalid."""


class CalendarNotFound(FastmailError):
    """The requested calendar does not exist."""

    def __init__(self, calendar_id: str) -> None:
        self.calendar_id = calendar_id
        super().__init__(f"Calendar not found: {calendar_id}")


class CalendarConflict(FastmailError):
    """Optimistic concurrency failure on a calendar write."""

    def __init__(self, calendar_id: str, sent_etag: str, server_etag: str | None) -> None:
        self.calendar_id = calendar_id
        self.sent_etag = sent_etag
        self.server_etag = server_etag
        super().__init__(
            f"Calendar conflict for '{calendar_id}': "
            f"sent ETag '{sent_etag}', server has '{server_etag}'"
        )


class EventNotFound(FastmailError):
    """The requested event does not exist."""

    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        super().__init__(f"Event not found: {event_id}")


class EventConflict(FastmailError):
    """Optimistic concurrency failure on an event write."""

    def __init__(self, event_id: str, sent_etag: str, server_etag: str | None) -> None:
        self.event_id = event_id
        self.sent_etag = sent_etag
        self.server_etag = server_etag
        super().__init__(
            f"Event conflict for '{event_id}': "
            f"sent ETag '{sent_etag}', server has '{server_etag}'"
        )


class CalDAVServerError(FastmailError):
    """The CalDAV server returned an unexpected response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)
