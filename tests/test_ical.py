"""Tests for iCalendar round-trip (parse ↔ serialize)."""

from fastmail_sdk.ical import (
    build_event_uid,
    parse_ical_event,
    serialize_ical_event,
)
from fastmail_sdk.models.event import (
    CalendarEvent,
    EventAttendee,
    EventDateTime,
    EventRecurrence,
    EventReminder,
)


def test_round_trip_simple_event():
    """A basic timed event survives a serialize → parse round-trip."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="Team Standup",
        start=EventDateTime(value="2026-07-22T09:00:00", timezone="America/Chicago"),
        end=EventDateTime(value="2026-07-22T09:30:00", timezone="America/Chicago"),
        location="Conference Room A",
        description="Daily sync",
    )
    ical = serialize_ical_event(event)
    parsed = parse_ical_event(ical, calendar_id="cal-1")

    assert parsed is not None
    assert parsed.title == "Team Standup"
    assert parsed.start.value == "2026-07-22T09:00:00"
    assert parsed.start.timezone == "America/Chicago"
    assert parsed.end.value == "2026-07-22T09:30:00"
    assert parsed.location == "Conference Room A"
    assert parsed.description == "Daily sync"
    assert not parsed.start.all_day


def test_round_trip_all_day_event():
    """All-day events use VALUE=DATE and round-trip correctly."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="Vacation",
        start=EventDateTime(value="2026-08-01", all_day=True),
        end=EventDateTime(value="2026-08-08", all_day=True),
    )
    ical = serialize_ical_event(event)
    parsed = parse_ical_event(ical, calendar_id="cal-1")

    assert parsed is not None
    assert parsed.title == "Vacation"
    assert parsed.start.all_day
    assert parsed.end.all_day
    assert parsed.start.value == "2026-08-01"
    assert parsed.end.value == "2026-08-08"


def test_round_trip_utc_event():
    """UTC events (ending in Z) round-trip correctly."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="UTC Meeting",
        start=EventDateTime(value="2026-07-22T14:00:00Z"),
        end=EventDateTime(value="2026-07-22T15:00:00Z"),
    )
    ical = serialize_ical_event(event)
    parsed = parse_ical_event(ical, calendar_id="cal-1")

    assert parsed is not None
    assert parsed.start.value == "2026-07-22T14:00:00Z"
    assert parsed.end.value == "2026-07-22T15:00:00Z"
    assert not parsed.start.all_day


def test_round_trip_with_recurrence():
    """Recurrence rules survive the round-trip."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="Weekly Sync",
        start=EventDateTime(value="2026-07-22T10:00:00"),
        end=EventDateTime(value="2026-07-22T10:30:00"),
        recurrence=EventRecurrence(frequency="WEEKLY", interval=1, by_day=["MO", "WE", "FR"]),
    )
    ical = serialize_ical_event(event)
    parsed = parse_ical_event(ical, calendar_id="cal-1")

    assert parsed is not None
    assert parsed.recurrence is not None
    assert parsed.recurrence.frequency == "WEEKLY"
    assert parsed.recurrence.interval == 1
    assert parsed.recurrence.by_day == ["MO", "WE", "FR"]


def test_round_trip_with_reminders():
    """Reminders (VALARM) survive the round-trip."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="Meeting with Reminder",
        start=EventDateTime(value="2026-07-22T15:00:00"),
        end=EventDateTime(value="2026-07-22T16:00:00"),
        reminders=[
            EventReminder(minutes_before=-30, action="DISPLAY"),
            EventReminder(minutes_before=-1440, action="EMAIL"),
        ],
    )
    ical = serialize_ical_event(event)
    parsed = parse_ical_event(ical, calendar_id="cal-1")

    assert parsed is not None
    assert len(parsed.reminders) == 2
    assert parsed.reminders[0].minutes_before == -30
    assert parsed.reminders[0].action == "DISPLAY"
    assert parsed.reminders[1].minutes_before == -1440
    assert parsed.reminders[1].action == "EMAIL"


def test_round_trip_with_attendees():
    """Attendees survive the round-trip."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="Design Review",
        start=EventDateTime(value="2026-07-22T11:00:00"),
        end=EventDateTime(value="2026-07-22T12:00:00"),
        attendees=[
            EventAttendee(email="alice@example.com", name="Alice", role="REQ-PARTICIPANT"),
            EventAttendee(email="bob@example.com", name="Bob", rsvp=True),
        ],
    )
    ical = serialize_ical_event(event)
    parsed = parse_ical_event(ical, calendar_id="cal-1")

    assert parsed is not None
    assert len(parsed.attendees) == 2
    assert parsed.attendees[0].email == "alice@example.com"
    assert parsed.attendees[0].name == "Alice"
    assert parsed.attendees[0].role == "REQ-PARTICIPANT"
    assert parsed.attendees[1].email == "bob@example.com"
    assert parsed.attendees[1].rsvp is True


def test_parse_preserves_href_and_etag():
    """CalDAV metadata (href, etag) is passed through to the model."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="Test",
        start=EventDateTime(value="2026-07-22T09:00:00"),
        end=EventDateTime(value="2026-07-22T10:00:00"),
    )
    ical = serialize_ical_event(event)
    parsed = parse_ical_event(
        ical,
        href="/dav/calendars/user/test@fastmail.com/Default/test.ics",
        etag='"abc123"',
        calendar_id="cal-1",
        calendar_name="Default",
    )

    assert parsed is not None
    assert parsed.href == "/dav/calendars/user/test@fastmail.com/Default/test.ics"
    assert parsed.etag == '"abc123"'
    assert parsed.calendar_name == "Default"


def test_parse_empty_text_returns_none():
    """Empty or invalid iCalendar text returns None."""
    assert parse_ical_event("") is None
    assert parse_ical_event("garbage") is None


def test_parse_missing_uid_returns_none():
    """A VEVENT without a UID is skipped."""
    ical = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:No UID
DTSTART:20260722T090000
DTEND:20260722T100000
END:VEVENT
END:VCALENDAR"""
    assert parse_ical_event(ical) is None


def test_serialize_preserves_uid():
    """The event UID is preserved in the serialized output."""
    uid = build_event_uid()
    event = CalendarEvent(
        id=uid,
        calendar_id="cal-1",
        title="Test",
        start=EventDateTime(value="2026-07-22T09:00:00"),
        end=EventDateTime(value="2026-07-22T10:00:00"),
    )
    ical = serialize_ical_event(event)
    assert uid in ical
    assert f"UID:{uid}" in ical


def test_serialize_includes_dtstamp():
    """Every serialized event includes a DTSTAMP."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="Test",
        start=EventDateTime(value="2026-07-22T09:00:00"),
        end=EventDateTime(value="2026-07-22T10:00:00"),
    )
    ical = serialize_ical_event(event)
    assert "DTSTAMP:" in ical


def test_round_trip_special_characters():
    """Characters that need iCalendar escaping survive the round-trip."""
    event = CalendarEvent(
        id=build_event_uid(),
        calendar_id="cal-1",
        title="Review: Q3; Budget, OKRs",
        start=EventDateTime(value="2026-07-22T09:00:00"),
        end=EventDateTime(value="2026-07-22T10:00:00"),
        description="Line 1\nLine 2 with \\ backslash",
    )
    ical = serialize_ical_event(event)
    parsed = parse_ical_event(ical, calendar_id="cal-1")

    assert parsed is not None
    assert parsed.title == "Review: Q3; Budget, OKRs"
    assert "Line 1\nLine 2 with \\ backslash" in parsed.description
