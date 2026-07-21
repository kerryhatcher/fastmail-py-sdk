"""Tests for CalDAV client using respx to mock HTTP."""

import pytest
from httpx import Response

from fastmail_sdk.caldav.client import CalDavClient, _parse_calendars_response
from fastmail_sdk.errors import CalendarNotFound, EventNotFound
from fastmail_sdk.models.event import CalendarEvent, EventDateTime, EventQuery


# ------------------------------------------------------------------
# XML response parsing
# ------------------------------------------------------------------

CALENDARS_MULTISTATUS = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:apple="http://apple.com/ns/ical/" xmlns:cs="http://calendarserver.org/ns/">
  <d:response>
    <d:href>/dav/calendars/user/test@fastmail.com/Default/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Default</d:displayname>
        <d:resourcetype>
          <c:calendar/>
        </d:resourcetype>
        <d:getetag>"etag-1"</d:getetag>
        <apple:calendar-color>#3a87ad</apple:calendar-color>
        <apple:calendar-order>1</apple:calendar-order>
        <cs:getctag>"ctag-1"</cs:getctag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/calendars/user/test@fastmail.com/Work/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Work</d:displayname>
        <d:resourcetype>
          <c:calendar/>
        </d:resourcetype>
        <d:getetag>"etag-2"</d:getetag>
        <apple:calendar-color>#ff6b6b</apple:calendar-color>
        <apple:calendar-order>2</apple:calendar-order>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


def test_parse_calendars_response():
    """Parse a valid multistatus response into Calendar objects."""
    calendars = _parse_calendars_response(CALENDARS_MULTISTATUS)

    assert len(calendars) == 2
    # Sorted by name
    assert calendars[0].name == "Default"
    assert calendars[0].id == "Default"
    assert calendars[0].color == "#3a87ad"
    assert calendars[0].etag == '"etag-1"'
    assert calendars[0].ctag == '"ctag-1"'
    assert calendars[0].is_default  # has /Default/ in href

    assert calendars[1].name == "Work"
    assert calendars[1].id == "Work"
    assert calendars[1].color == "#ff6b6b"
    assert not calendars[1].is_default


def test_parse_calendars_response_empty():
    """An empty multistatus returns an empty list."""
    empty = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
</d:multistatus>"""
    calendars = _parse_calendars_response(empty)
    assert calendars == []


# ------------------------------------------------------------------
# Client tests with mocked HTTP
# ------------------------------------------------------------------


@pytest.fixture
def client():
    return CalDavClient("test@fastmail.com", "app-password")


@pytest.fixture
def calendar_home_xml():
    return """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/principals/user/test@fastmail.com/</d:href>
    <d:propstat>
      <d:prop>
        <c:calendar-home-set>
          <d:href>/dav/calendars/user/test@fastmail.com/</d:href>
        </c:calendar-home-set>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


def test_list_calendars(respx_mock, client, calendar_home_xml):
    """list_calendars discovers the home and fetches calendars."""
    # PROPFIND on principal → calendar-home-set
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/principals/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=calendar_home_xml))

    # PROPFIND on calendar home → calendar list
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=CALENDARS_MULTISTATUS))

    async def run():
        async with client:
            calendars = await client.list_calendars()
            assert len(calendars) == 2
            assert calendars[0].name == "Default"

    import asyncio
    asyncio.run(run())


def test_get_calendar_by_id_found(respx_mock, client, calendar_home_xml):
    """get_calendar_by_id returns the matching calendar."""
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/principals/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=calendar_home_xml))
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=CALENDARS_MULTISTATUS))

    async def run():
        async with client:
            cal = await client.get_calendar_by_id("Work")
            assert cal.name == "Work"
            assert cal.color == "#ff6b6b"

    import asyncio
    asyncio.run(run())


def test_get_calendar_by_id_not_found(respx_mock, client, calendar_home_xml):
    """get_calendar_by_id raises CalendarNotFound for unknown IDs."""
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/principals/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=calendar_home_xml))
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=CALENDARS_MULTISTATUS))

    async def run():
        async with client:
            with pytest.raises(CalendarNotFound):
                await client.get_calendar_by_id("nonexistent")

    import asyncio
    asyncio.run(run())


EVENTS_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/calendars/user/test@fastmail.com/Default/evt-1.ics</d:href>
    <d:propstat>
      <d:prop>
        <d:getetag>"etag-ev1"</d:getetag>
        <c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//fastmail//EN
BEGIN:VEVENT
UID:evt-1
DTSTAMP:20260721T000000Z
SUMMARY:Team Standup
DTSTART;TZID=America/Chicago:20260722T090000
DTEND;TZID=America/Chicago:20260722T093000
LOCATION:Conference Room A
END:VEVENT
END:VCALENDAR</c:calendar-data>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


def test_list_events(respx_mock, client, calendar_home_xml):
    """list_events fetches and parses events from all calendars."""
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/principals/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=calendar_home_xml))
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=CALENDARS_MULTISTATUS))
    # REPORT on Default calendar
    respx_mock.request(
        "REPORT", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/Default/",
    ).mock(return_value=Response(207, content=EVENTS_REPORT))
    # REPORT on Work calendar (empty)
    respx_mock.request(
        "REPORT", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/Work/",
    ).mock(return_value=Response(207, content="""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
</d:multistatus>"""))

    async def run():
        async with client:
            events = await client.list_events(EventQuery())
            assert len(events) == 1
            assert events[0].title == "Team Standup"
            assert events[0].id == "evt-1"
            assert events[0].calendar_id == "Default"
            assert events[0].start.value == "2026-07-22T09:00:00"
            assert events[0].start.timezone == "America/Chicago"

    import asyncio
    asyncio.run(run())


def test_get_event_by_id_found(respx_mock, client, calendar_home_xml):
    """get_event_by_id finds an event via UID REPORT."""
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/principals/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=calendar_home_xml))
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=CALENDARS_MULTISTATUS))
    # UID REPORT on Default
    respx_mock.request(
        "REPORT", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/Default/",
    ).mock(return_value=Response(207, content=EVENTS_REPORT))
    # UID REPORT on Work (no match)
    respx_mock.request(
        "REPORT", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/Work/",
    ).mock(return_value=Response(207, content="""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
</d:multistatus>"""))

    async def run():
        async with client:
            event = await client.get_event_by_id("evt-1")
            assert event.title == "Team Standup"

    import asyncio
    asyncio.run(run())


def test_get_event_by_id_not_found(respx_mock, client, calendar_home_xml):
    """get_event_by_id raises EventNotFound when no calendar has the event."""
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/principals/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=calendar_home_xml))
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=CALENDARS_MULTISTATUS))
    # Both calendars return empty
    empty = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
</d:multistatus>"""
    respx_mock.request(
        "REPORT", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/Default/",
    ).mock(return_value=Response(207, content=empty))
    respx_mock.request(
        "REPORT", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/Work/",
    ).mock(return_value=Response(207, content=empty))

    async def run():
        async with client:
            with pytest.raises(EventNotFound):
                await client.get_event_by_id("nonexistent")

    import asyncio
    asyncio.run(run())


def test_create_event(respx_mock, client, calendar_home_xml):
    """create_event PUTs an iCalendar body and returns the created event."""
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/principals/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=calendar_home_xml))
    respx_mock.request(
        "PROPFIND", "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/",
    ).mock(return_value=Response(207, content=CALENDARS_MULTISTATUS))
    respx_mock.put(
        "https://caldav.fastmail.com/dav/calendars/user/test@fastmail.com/Default/new-event.ics",
    ).mock(return_value=Response(
        201,
        headers={
            "ETag": '"new-etag"',
            "Location": "/dav/calendars/user/test@fastmail.com/Default/new-event.ics",
        },
    ))

    async def run():
        async with client:
            event = CalendarEvent(
                id="new-event",
                calendar_id="",
                title="New Meeting",
                start=EventDateTime(value="2026-07-22T14:00:00"),
                end=EventDateTime(value="2026-07-22T15:00:00"),
            )
            created = await client.create_event(None, event)
            assert created.etag == '"new-etag"'
            assert created.calendar_id == "Default"
            assert created.calendar_name == "Default"

    import asyncio
    asyncio.run(run())
