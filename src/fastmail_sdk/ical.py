"""iCalendar ↔ CalendarEvent round-trip.

Parses CalDAV iCalendar (VEVENT) data into :class:`CalendarEvent` models and
serializes them back to valid iCalendar text. Uses the ``icalendar`` library
for parsing; serialization is hand-rolled for precise control over output
format (matching Fastmail's expected iCalendar dialect).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent

from fastmail_sdk.models.event import (
    CalendarEvent,
    EventAttendee,
    EventDateTime,
    EventRecurrence,
    EventReminder,
)

# ---------------------------------------------------------------------------
# Parse: iCalendar text → CalendarEvent
# ---------------------------------------------------------------------------

# RFC 5545 line folding: lines longer than 75 octets are continued with
# CRLF + one whitespace character on the next line.
_FOLD_RE = re.compile(r"\r?\n[ \t]")


def _unfold(ical_text: str) -> str:
    """Undo RFC 5545 line folding."""
    return _FOLD_RE.sub("", ical_text)


def _extract_value(line: str) -> str:
    """Return the value portion of a content line (after the first colon)."""
    return line.split(":", 1)[1] if ":" in line else ""


def _unescape(value: str) -> str:
    """Reverse iCalendar text escaping."""
    result = []
    chars = iter(value)
    for ch in chars:
        if ch == "\\":
            try:
                nxt = next(chars)
            except StopIteration:
                result.append(ch)
                break
            if nxt in ("\\", ";", ","):
                result.append(nxt)
            elif nxt in ("n", "N"):
                result.append("\n")
            else:
                result.append(ch)
                result.append(nxt)
        else:
            result.append(ch)
    return "".join(result)


def _parse_datetime(line: str) -> EventDateTime:
    """Parse a DTSTART or DTEND property into an EventDateTime."""
    prop, _, value = line.partition(":")
    params = prop.split(";")[1:] if ";" in prop else []

    timezone = None
    all_day = False
    for param in params:
        if param.startswith("TZID="):
            timezone = param[5:]
        elif param == "VALUE=DATE":
            all_day = True

    if all_day:
        # YYYYMMDD → YYYY-MM-DD
        if len(value) == 8 and value.isdigit():
            value = f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        return EventDateTime(value=value, timezone=timezone, all_day=True)

    if value.endswith("Z"):
        # YYYYMMDDTHHMMSSZ → YYYY-MM-DDTHH:MM:SSZ
        stripped = value[:-1]
        if len(stripped) == 15 and stripped[:8].isdigit():
            value = (
                f"{stripped[:4]}-{stripped[4:6]}-{stripped[6:8]}T"
                f"{stripped[9:11]}:{stripped[11:13]}:{stripped[13:15]}Z"
            )
        return EventDateTime(value=value, timezone=None, all_day=False)

    # Floating local time: YYYYMMDDTHHMMSS → YYYY-MM-DDTHH:MM:SS
    if len(value) == 15 and value[:8].isdigit():
        value = (
            f"{value[:4]}-{value[4:6]}-{value[6:8]}T"
            f"{value[9:11]}:{value[11:13]}:{value[13:15]}"
        )
    return EventDateTime(value=value, timezone=timezone, all_day=False)


def _parse_attendee(line: str) -> EventAttendee | None:
    """Parse an ATTENDEE property."""
    prop, _, email = line.partition(":")
    email = email.removeprefix("mailto:")
    if not email:
        return None

    attendee = EventAttendee(email=email)
    for param in prop.split(";")[1:]:
        if param.startswith("CN="):
            attendee.name = _unescape(param[3:])
        elif param.startswith("ROLE="):
            attendee.role = param[5:]
        elif param.startswith("PARTSTAT="):
            attendee.partstat = param[6:]
        elif param.startswith("RSVP="):
            attendee.rsvp = param[5:].upper() == "TRUE"
    return attendee


def _parse_rrule(value: str) -> EventRecurrence | None:
    """Parse an RRULE value string."""
    recurrence = EventRecurrence(frequency="")
    for part in value.split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        if key == "FREQ":
            recurrence.frequency = val
        elif key == "INTERVAL":
            recurrence.interval = int(val)
        elif key == "COUNT":
            recurrence.count = int(val)
        elif key == "UNTIL":
            recurrence.until = val
        elif key == "BYDAY":
            recurrence.by_day = val.split(",")
    return recurrence if recurrence.frequency else None


def _parse_trigger_minutes(value: str) -> int:
    """Parse a TRIGGER duration value into minutes-before (negative = before start).

    RFC 5545 duration: ["+" / "-"] "P" [dur-date / dur-time / dur-week]
    Examples: -PT15M, PT1H, -P1D, P1W
    """
    value = value.strip()
    sign = -1 if value.startswith("-") else 1
    if value.startswith("+") or value.startswith("-"):
        value = value[1:]
    if value.startswith("P"):
        value = value[1:]
    total_minutes = 0
    for match in re.finditer(r"(\d+)([WDT]?)([HMS])", value):
        num = int(match.group(1))
        unit = match.group(3)
        if unit == "W":
            total_minutes += num * 7 * 24 * 60
        elif unit == "D":
            total_minutes += num * 24 * 60
        elif unit == "H":
            total_minutes += num * 60
        elif unit == "M":
            total_minutes += num
        elif unit == "S":
            total_minutes += num // 60
    return sign * total_minutes


def parse_ical_event(
    ical_text: str,
    href: str | None = None,
    etag: str | None = None,
    calendar_id: str = "",
    calendar_name: str | None = None,
) -> CalendarEvent | None:
    """Parse a single VEVENT from iCalendar text.

    Args:
        ical_text: Raw iCalendar data (may contain VCALENDAR wrapper).
        href: CalDAV resource path.
        etag: Current ETag.
        calendar_id: Parent calendar resource ID.
        calendar_name: Parent calendar display name.

    Returns:
        A CalendarEvent, or None if the text contains no valid VEVENT.
    """
    unfolded = _unfold(ical_text)

    # Try icalendar for structural parsing (best-effort; some servers produce
    # non-standard iCalendar that icalendar rejects, so we fall back to manual
    # line-by-line parsing on any error).
    uid = ""
    title = ""
    try:
        cal = ICalendar.from_ical(unfolded)
        for component in cal.walk():
            if component.name == "VEVENT":
                uid = str(component.get("uid", ""))
                title = str(component.get("summary", ""))
                break
    except Exception:
        pass

    # Manual line-by-line parse (always run — more control over format)
    start = EventDateTime(value="")
    end = EventDateTime(value="")
    attendees: list[EventAttendee] = []
    recurrence = None
    reminders: list[EventReminder] = []
    location = None
    description = None

    in_alarm = False
    current_alarm_action = "DISPLAY"
    current_alarm_minutes = 0

    for raw_line in unfolded.splitlines():
        line = raw_line.strip()
        if line == "BEGIN:VALARM":
            in_alarm = True
            current_alarm_action = "DISPLAY"
            current_alarm_minutes = 0
            continue
        if line == "END:VALARM":
            in_alarm = False
            reminders.append(
                EventReminder(
                    minutes_before=current_alarm_minutes,
                    action=current_alarm_action,
                )
            )
            continue

        if in_alarm:
            if line.startswith("ACTION"):
                current_alarm_action = _extract_value(line)
            elif line.startswith("TRIGGER"):
                current_alarm_minutes = _parse_trigger_minutes(_extract_value(line))
            continue

        if line.startswith("UID") and not uid:
            uid = _extract_value(line)
        elif line.startswith("SUMMARY") and not title:
            title = _extract_value(line)
        elif line.startswith("DTSTART"):
            start = _parse_datetime(line)
        elif line.startswith("DTEND"):
            end = _parse_datetime(line)
        elif line.startswith("LOCATION"):
            location = _extract_value(line)
        elif line.startswith("DESCRIPTION"):
            description = _unescape(_extract_value(line))
        elif line.startswith("ATTENDEE"):
            if att := _parse_attendee(line):
                attendees.append(att)
        elif line.startswith("RRULE"):
            recurrence = _parse_rrule(_extract_value(line))

    if not uid or not title or not start.value or not end.value:
        return None

    return CalendarEvent(
        id=uid,
        calendar_id=calendar_id,
        calendar_name=calendar_name,
        href=href,
        etag=etag,
        title=title,
        start=start,
        end=end,
        location=location,
        description=description,
        attendees=attendees,
        recurrence=recurrence,
        reminders=reminders,
    )


# ---------------------------------------------------------------------------
# Serialize: CalendarEvent → iCalendar text
# ---------------------------------------------------------------------------

# RFC 5545 §3.3.10 valid FREQ values
_VALID_FREQ = frozenset(
    {"SECONDLY", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
)

# RFC 5545 valid ATTENDEE ROLE values
_VALID_ROLE = frozenset(
    {"CHAIR", "REQ-PARTICIPANT", "OPT-PARTICIPANT", "NON-PARTICIPANT"}
)

# RFC 5545 valid ATTENDEE PARTSTAT values
_VALID_PARTSTAT = frozenset(
    {
        "NEEDS-ACTION",
        "ACCEPTED",
        "DECLINED",
        "TENTATIVE",
        "DELEGATED",
        "COMPLETED",
        "IN-PROCESS",
    }
)


def _escape(value: str) -> str:
    """Apply iCalendar text escaping."""
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Apply RFC 5545 line folding (75-octet max per line)."""
    if len(line) <= 75:
        return line + "\r\n"
    result = []
    remaining = line
    first = True
    while remaining:
        limit = 75 if first else 74
        first = False
        if len(remaining) <= limit:
            result.append(remaining)
            break
        # Find a safe split point
        end = limit
        while end > 0 and (remaining[end - 1] & 0xC0) == 0x80:
            end -= 1
        result.append(remaining[:end])
        remaining = " " + remaining[end:]
    return "\r\n".join(result) + "\r\n"


def _serialize_datetime(name: str, dt: EventDateTime) -> str:
    """Serialize a DTSTART or DTEND property."""
    if dt.all_day:
        # YYYY-MM-DD → YYYYMMDD
        value = dt.value.replace("-", "")
        return f"{name};VALUE=DATE:{value}"

    if dt.value.endswith("Z"):
        # YYYY-MM-DDTHH:MM:SSZ → YYYYMMDDTHHMMSSZ
        value = dt.value.replace("-", "").replace(":", "")
        return f"{name}:{value}"

    # Floating local time
    value = dt.value.replace("-", "").replace(":", "")
    if dt.timezone:
        return f"{name};TZID={dt.timezone}:{value}"
    return f"{name}:{value}"


def _serialize_attendee(att: EventAttendee) -> str:
    """Serialize an ATTENDEE property."""
    parts = ["ATTENDEE"]
    if att.name:
        parts.append(f"CN={_escape(att.name)}")
    if att.role and att.role.upper() in _VALID_ROLE:
        parts.append(f"ROLE={att.role.upper()}")
    if att.partstat and att.partstat.upper() in _VALID_PARTSTAT:
        parts.append(f"PARTSTAT={att.partstat.upper()}")
    if att.rsvp is not None:
        parts.append(f"RSVP={'TRUE' if att.rsvp else 'FALSE'}")
    return f"{';'.join(parts)}:mailto:{_escape(att.email)}"


def _serialize_rrule(rrule: EventRecurrence) -> str:
    """Serialize an RRULE property."""
    freq = rrule.frequency.upper() if rrule.frequency.upper() in _VALID_FREQ else ""
    parts = [f"FREQ={freq}"]
    if rrule.interval is not None:
        parts.append(f"INTERVAL={rrule.interval}")
    if rrule.count is not None:
        parts.append(f"COUNT={rrule.count}")
    if rrule.until:
        parts.append(f"UNTIL={rrule.until}")
    if rrule.by_day:
        parts.append(f"BYDAY={','.join(rrule.by_day)}")
    return f"RRULE:{';'.join(parts)}"


def _serialize_trigger(minutes_before: int) -> str:
    """Serialize a TRIGGER value (negative = before start).

    RFC 5545 duration format: ["+" / "-"] "P" ["T"] dur-value.
    Minutes always use the "T" prefix (dur-time).
    """
    sign = "-" if minutes_before < 0 else ""
    return f"TRIGGER:{sign}PT{abs(minutes_before)}M"


def serialize_ical_event(event: CalendarEvent) -> str:
    """Serialize a CalendarEvent to iCalendar text (VCALENDAR + VEVENT).

    Returns a complete iCalendar document suitable for PUT to a CalDAV server.
    """
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//fastmail-py-sdk//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{_escape(event.id)}",
        f"DTSTAMP:{now}",
        f"SUMMARY:{_escape(event.title)}",
        _serialize_datetime("DTSTART", event.start),
        _serialize_datetime("DTEND", event.end),
    ]

    if event.location:
        lines.append(f"LOCATION:{_escape(event.location)}")
    if event.description:
        lines.append(f"DESCRIPTION:{_escape(event.description)}")

    for att in event.attendees:
        lines.append(_serialize_attendee(att))

    if event.recurrence:
        lines.append(_serialize_rrule(event.recurrence))

    for reminder in event.reminders:
        lines.append("BEGIN:VALARM")
        lines.append(f"ACTION:{reminder.action}")
        lines.append(_serialize_trigger(reminder.minutes_before))
        lines.append("DESCRIPTION:Reminder")
        lines.append("END:VALARM")

    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    return "".join(_fold(line) for line in lines)


def build_event_uid() -> str:
    """Generate a new unique event UID (UUID v4)."""
    return str(uuid.uuid4())


# ------------------------------------------------------------------
# Date range helpers (ported from the Rust fastmail-cli)
# ------------------------------------------------------------------


def default_today_range() -> tuple[datetime, datetime]:
    """Return (start, end) covering the rest of today in UTC."""
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = tomorrow.replace(day=now.day + 1) if now.hour > 0 else tomorrow
    # Actually: from now until end of tomorrow
    from datetime import timedelta

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    return now, today_end


def current_week_range() -> tuple[datetime, datetime]:
    """Return (monday 00:00, next monday 00:00) in UTC."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    monday = now.replace(hour=0, minute=0, second=0, microsecond=0)
    monday -= timedelta(days=monday.weekday())
    next_monday = monday + timedelta(days=7)
    return monday, next_monday


def parse_range_start(value: str) -> datetime:
    """Parse a user-supplied range start into a UTC datetime."""
    return _parse_user_range(value, end_of_day=False)


def parse_range_end(value: str) -> datetime:
    """Parse a user-supplied range end into a UTC datetime."""
    return _parse_user_range(value, end_of_day=True)


def _parse_user_range(value: str, end_of_day: bool) -> datetime:
    """Parse a date or datetime string into a UTC datetime."""
    from datetime import timedelta

    # Try RFC 3339 first
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    # Try YYYY-MM-DD
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            dt += timedelta(days=1)
        return dt
    except ValueError:
        pass

    # Try YYYY-MM-DDTHH:MM or YYYY-MM-DDTHH:MM:SS
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    raise ValueError(f"Invalid date/time '{value}'. Use YYYY-MM-DD or RFC 3339.")
