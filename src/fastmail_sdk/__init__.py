"""Fastmail Python SDK — JMAP, CalDAV, and CardDAV clients for Fastmail."""

from fastmail_sdk.caldav.client import CalDavClient
from fastmail_sdk.carddav.client import CardDavClient
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
from fastmail_sdk.jmap.client import JmapClient
from fastmail_sdk.models.calendar import Calendar
from fastmail_sdk.models.contacts import (
    AddressBook,
    Contact,
    ContactCreateResult,
    ContactEmail,
    ContactGroup,
    ContactPhone,
)
from fastmail_sdk.models.email import (
    Email,
    EmailAddress,
    EmailBodyPart,
    EmailBodyValue,
    Identity,
    Mailbox,
    MaskedEmail,
    SearchFilter,
    Session,
)
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
    # Clients
    "CalDavClient",
    "CardDavClient",
    "JmapClient",
    # Calendar models
    "Calendar",
    "CalendarEvent",
    "EventAttendee",
    "EventCreate",
    "EventQuery",
    "EventRecurrence",
    "EventReminder",
    "EventUpdate",
    # Contact models
    "AddressBook",
    "Contact",
    "ContactCreateResult",
    "ContactEmail",
    "ContactGroup",
    "ContactPhone",
    # Email models
    "Email",
    "EmailAddress",
    "EmailBodyPart",
    "EmailBodyValue",
    "Identity",
    "Mailbox",
    "MaskedEmail",
    "SearchFilter",
    "Session",
    # Errors
    "CalDAVServerError",
    "CalendarConflict",
    "CalendarNotFound",
    "EventConflict",
    "EventNotFound",
    "FastmailError",
    "NotAuthenticated",
    # Config
    "load_credentials",
]
