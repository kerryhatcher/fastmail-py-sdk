"""CalDAV client for Fastmail calendars and events.

Talks CalDAV over HTTP using ``httpx``. Ports the well-tested logic from the
Rust ``fastmail-cli`` tool: concurrent per-calendar fetches with partial-failure
tolerance, ETag-based optimistic concurrency, UID REPORT fallback, and
iCalendar round-tripping via :mod:`fastmail_sdk.ical`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx

from fastmail_sdk.caldav.xml_helpers import (
    calendar_home_propfind,
    calendar_query_body,
    list_calendars_propfind,
    mkcalendar_body,
    proppatch_calendar_body,
    uid_report_body,
)
from fastmail_sdk.errors import (
    CalDAVServerError,
    CalendarConflict,
    CalendarNotFound,
    EventConflict,
    EventNotFound,
)
from fastmail_sdk.ical import build_event_uid, parse_ical_event, serialize_ical_event
from fastmail_sdk.models.calendar import Calendar
from fastmail_sdk.models.event import CalendarEvent, EventQuery

logger = logging.getLogger(__name__)

# Fastmail's CalDAV endpoint
CALDAV_BASE = "https://caldav.fastmail.com"

# XML namespaces
_DAV_NS = "DAV:"
_CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
_APPLE_ICAL_NS = "http://apple.com/ns/ical/"
_CS_NS = "http://calendarserver.org/ns/"


def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


class CalDavClient:
    """Async CalDAV client for Fastmail.

    Args:
        username: Fastmail email address.
        app_password: Fastmail app password (not API token).
        base_url: CalDAV base URL (defaults to Fastmail's production endpoint).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        username: str,
        app_password: str,
        base_url: str = CALDAV_BASE,
        timeout: float = 30.0,
    ) -> None:
        self.username = username
        self.app_password = app_password
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("CalDavClient not opened — use 'async with client:'")
        return self._client

    async def __aenter__(self) -> CalDavClient:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            auth=(self.username, self.app_password),
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Calendar home discovery
    # ------------------------------------------------------------------

    async def discover_calendar_home(self) -> str:
        """Discover the calendar-home-set URL via PROPFIND on the principal."""
        # Try principal-based discovery first
        try:
            return await self._discover_via_principal()
        except CalDAVServerError:
            pass

        # Fall back to well-known path
        return f"/dav/calendars/user/{self.username}/"

    async def _discover_via_principal(self) -> str:
        principal_href = f"/dav/principals/user/{self.username}/"
        url = f"{self.base_url}{principal_href}"
        response = await self.client.request(
            "PROPFIND", url, content=calendar_home_propfind(),
            headers={"Content-Type": "application/xml", "Depth": "0"},
        )
        if response.status_code not in (207, 200):
            raise CalDAVServerError(
                f"Calendar-home discovery failed: {response.status_code}",
                response.status_code,
            )

        root = ET.fromstring(response.text)
        for chs in root.findall(f".//{_tag(_CALDAV_NS, 'calendar-home-set')}"):
            for href in chs.findall(f".//{_tag(_DAV_NS, 'href')}"):
                if href.text:
                    return href.text.strip()

        raise CalDAVServerError("calendar-home-set not found in PROPFIND response")

    # ------------------------------------------------------------------
    # Calendar CRUD
    # ------------------------------------------------------------------

    async def list_calendars(self) -> list[Calendar]:
        """List all calendars in the user's calendar home."""
        home = await self.discover_calendar_home()
        url = f"{self.base_url}{home}"
        response = await self.client.request(
            "PROPFIND", url, content=list_calendars_propfind(),
            headers={"Content-Type": "application/xml", "Depth": "1"},
        )
        if response.status_code not in (207, 200):
            raise CalDAVServerError(
                f"Calendar PROPFIND failed: {response.status_code}",
                response.status_code,
            )

        return _parse_calendars_response(response.text)

    async def default_calendar(self) -> Calendar:
        """Return the default/primary calendar."""
        calendars = await self.list_calendars()
        for cal in calendars:
            if cal.is_default:
                return cal
        if calendars:
            return calendars[0]
        raise CalendarNotFound("default")

    async def get_calendar_by_id(self, calendar_id: str) -> Calendar:
        """Look up a calendar by its resource ID."""
        calendars = await self.list_calendars()
        for cal in calendars:
            if cal.id == calendar_id:
                return cal
        raise CalendarNotFound(calendar_id)

    async def create_calendar(self, name: str, color: str | None = None) -> Calendar:
        """Create a new calendar collection."""
        home = await self.discover_calendar_home()
        slug = _slugify(name)
        uid = build_event_uid()[:8]
        href = f"{home.rstrip('/')}/{slug}-{uid}/"
        url = f"{self.base_url}{href}"

        response = await self.client.request(
            "MKCALENDAR", url, content=mkcalendar_body(name, color),
            headers={"Content-Type": "application/xml"},
        )
        if response.status_code not in (201, 200, 204):
            raise CalDAVServerError(
                f"MKCALENDAR failed: {response.status_code} - {response.text}",
                response.status_code,
            )

        created_href = response.headers.get("Location", href)
        if created_href.startswith(self.base_url):
            created_href = created_href[len(self.base_url):]

        return Calendar(
            id=_resource_id_from_href(created_href),
            name=name,
            color=color,
            href=created_href,
            etag=response.headers.get("ETag"),
            is_default=False,
        )

    async def update_calendar(
        self, calendar: Calendar, name: str | None = None, color: str | None = None,
    ) -> Calendar:
        """Update a calendar's display name and/or color."""
        url = f"{self.base_url}{calendar.href}"
        headers: dict[str, str] = {"Content-Type": "application/xml"}
        if calendar.etag:
            headers["If-Match"] = calendar.etag

        new_name = name if name is not None else calendar.name
        new_color = color if color is not None else calendar.color

        response = await self.client.request(
            "PROPPATCH", url, content=proppatch_calendar_body(new_name, new_color),
            headers=headers,
        )
        if response.status_code not in (200, 204, 207):
            if response.status_code == 412:
                raise CalendarConflict(
                    calendar.id,
                    calendar.etag or "",
                    response.headers.get("ETag"),
                )
            if response.status_code == 404:
                raise CalendarNotFound(calendar.id)
            raise CalDAVServerError(
                f"PROPPATCH failed: {response.status_code}",
                response.status_code,
            )

        updated = calendar.model_copy()
        updated.name = new_name
        updated.color = new_color
        updated.etag = response.headers.get("ETag") or calendar.etag
        return updated

    async def delete_calendar(self, calendar: Calendar) -> None:
        """Delete a calendar collection."""
        url = f"{self.base_url}{calendar.href}"
        headers: dict[str, str] = {}
        if calendar.etag:
            headers["If-Match"] = calendar.etag

        response = await self.client.delete(url, headers=headers)
        if response.status_code in (204, 200, 202):
            return

        # Retry without ETag on 412 with no replacement ETag
        if response.status_code == 412 and calendar.etag and not response.headers.get("ETag"):
            response = await self.client.delete(url)
            if response.status_code in (204, 200, 202):
                return

        if response.status_code == 412:
            raise CalendarConflict(
                calendar.id,
                calendar.etag or "",
                response.headers.get("ETag"),
            )
        if response.status_code == 404:
            raise CalendarNotFound(calendar.id)
        raise CalDAVServerError(
            f"DELETE calendar failed: {response.status_code}",
            response.status_code,
        )

    # ------------------------------------------------------------------
    # Event CRUD
    # ------------------------------------------------------------------

    async def list_events(self, query: EventQuery) -> list[CalendarEvent]:
        """List events, optionally filtered by calendar and date range.

        Fetches from all calendars concurrently; individual calendar failures
        are tolerated (logged and skipped).
        """
        calendars = await self._resolve_calendars(query.calendar_id)

        start_str = query.start.strftime("%Y%m%dT%H%M%SZ") if query.start else None
        end_str = query.end.strftime("%Y%m%dT%H%M%SZ") if query.end else None

        async def fetch(cal: Calendar) -> list[CalendarEvent]:
            try:
                return await self._list_events_in_calendar(cal, start_str, end_str)
            except Exception:
                logger.warning("Failed to fetch events from calendar %s", cal.id, exc_info=True)
                return []

        results = await asyncio.gather(*(fetch(cal) for cal in calendars))
        all_events: list[CalendarEvent] = []
        for events in results:
            all_events.extend(events)

        all_events.sort(key=lambda e: e.start.value)
        return all_events

    async def _list_events_in_calendar(
        self, calendar: Calendar, start: str | None, end: str | None,
    ) -> list[CalendarEvent]:
        url = f"{self.base_url}{calendar.href}"
        response = await self.client.request(
            "REPORT", url, content=calendar_query_body(start, end),
            headers={"Content-Type": "application/xml", "Depth": "1"},
        )
        if response.status_code not in (207, 200):
            raise CalDAVServerError(
                f"Event REPORT failed for {calendar.id}: {response.status_code}",
                response.status_code,
            )

        return _parse_events_response(
            response.text, calendar.id, calendar.name, start, end,
        )

    async def get_event_by_id(
        self, event_id: str, calendar_id: str | None = None,
    ) -> CalendarEvent:
        """Fetch a single event by UID.

        Tries a UID REPORT first; falls back to a full calendar scan if the
        server doesn't support it (matching the Rust D-04/D-05 pattern).
        """
        calendars = await self._resolve_calendars(calendar_id)
        needs_fallback = False

        async def fetch(cal: Calendar) -> CalendarEvent | None:
            nonlocal needs_fallback
            url = f"{self.base_url}{cal.href}"
            try:
                response = await self.client.request(
                    "REPORT", url, content=uid_report_body(event_id),
                    headers={"Content-Type": "application/xml", "Depth": "1"},
                )
            except httpx.HTTPError:
                logger.warning("UID REPORT request failed for calendar %s", cal.id, exc_info=True)
                return None

            if response.status_code in (400, 501):
                needs_fallback = True
                return None
            if response.status_code not in (207, 200):
                logger.warning(
                    "UID REPORT returned %s for calendar %s",
                    response.status_code, cal.id,
                )
                return None

            events = _parse_events_response(
                response.text, cal.id, cal.name, None, None,
            )
            for event in events:
                if event.id == event_id:
                    return event
            return None

        results = await asyncio.gather(*(fetch(cal) for cal in calendars))
        for result in results:
            if result is not None:
                return result

        if needs_fallback:
            logger.info("UID REPORT unsupported, falling back to full fetch for %s", event_id)
            return await self._get_event_by_id_full_fetch(event_id, calendar_id)

        raise EventNotFound(event_id)

    async def _get_event_by_id_full_fetch(
        self, event_id: str, calendar_id: str | None,
    ) -> CalendarEvent:
        """Fallback: scan all events in the target calendar(s)."""
        calendars = await self._resolve_calendars(calendar_id)
        for cal in calendars:
            try:
                events = await self._list_events_in_calendar(cal, None, None)
            except Exception:
                logger.warning("Full fetch failed for calendar %s", cal.id, exc_info=True)
                continue
            for event in events:
                if event.id == event_id:
                    return event
        raise EventNotFound(event_id)

    async def create_event(
        self, calendar_id: str | None, event: CalendarEvent,
    ) -> CalendarEvent:
        """Create a new event in the specified calendar (or the default)."""
        calendar = await self._resolve_calendar(calendar_id)
        href = _build_event_href(calendar.href, event.id)
        url = f"{self.base_url}{href}"
        body = serialize_ical_event(event)

        response = await self.client.put(
            url, content=body,
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                "If-None-Match": "*",
            },
        )
        if response.status_code not in (201, 204, 200):
            if response.status_code == 412:
                raise EventConflict(event.id, "*", response.headers.get("ETag"))
            raise CalDAVServerError(
                f"Event PUT (create) failed: {response.status_code} - {response.text}",
                response.status_code,
            )

        created = event.model_copy()
        created.calendar_id = calendar.id
        created.calendar_name = calendar.name
        created.href = response.headers.get("Location", href)
        if created.href and created.href.startswith(self.base_url):
            created.href = created.href[len(self.base_url):]
        created.etag = response.headers.get("ETag")
        return created

    async def update_event(
        self, event: CalendarEvent, previous_etag: str,
    ) -> CalendarEvent:
        """Update an existing event (requires the current ETag)."""
        if not event.href:
            raise EventNotFound(event.id)
        url = f"{self.base_url}{event.href}"
        body = serialize_ical_event(event)

        response = await self.client.put(
            url, content=body,
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                "If-Match": previous_etag,
            },
        )
        if response.status_code not in (200, 204, 201):
            if response.status_code == 412:
                raise EventConflict(
                    event.id, previous_etag, response.headers.get("ETag"),
                )
            if response.status_code == 404:
                raise EventNotFound(event.id)
            raise CalDAVServerError(
                f"Event PUT (update) failed: {response.status_code} - {response.text}",
                response.status_code,
            )

        updated = event.model_copy()
        updated.etag = response.headers.get("ETag") or event.etag
        return updated

    async def delete_event(self, event: CalendarEvent) -> None:
        """Delete an event (requires href and ETag)."""
        if not event.href:
            raise EventNotFound(event.id)
        if not event.etag:
            raise EventConflict(event.id, "", None)

        url = f"{self.base_url}{event.href}"
        response = await self.client.delete(url, headers={"If-Match": event.etag})
        if response.status_code in (204, 200, 202):
            return

        if response.status_code == 412:
            raise EventConflict(
                event.id, event.etag, response.headers.get("ETag"),
            )
        if response.status_code == 404:
            raise EventNotFound(event.id)
        raise CalDAVServerError(
            f"Event DELETE failed: {response.status_code}",
            response.status_code,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_calendars(self, calendar_id: str | None) -> list[Calendar]:
        if calendar_id:
            return [await self.get_calendar_by_id(calendar_id)]
        return await self.list_calendars()

    async def _resolve_calendar(self, calendar_id: str | None) -> Calendar:
        if calendar_id:
            return await self.get_calendar_by_id(calendar_id)
        return await self.default_calendar()


# ------------------------------------------------------------------
# XML response parsers
# ------------------------------------------------------------------


def _parse_calendars_response(xml: str) -> list[Calendar]:
    """Parse a PROPFIND multistatus response into Calendar objects."""
    root = ET.fromstring(xml)
    calendars: list[Calendar] = []
    default_id: str | None = None
    best_order: int | None = None

    for response in root.findall(f".//{_tag(_DAV_NS, 'response')}"):
        href_el = response.find(f"{_tag(_DAV_NS, 'href')}")
        href = href_el.text.strip() if href_el is not None and href_el.text else ""

        # Only calendar collections
        is_calendar = response.find(f".//{_tag(_CALDAV_NS, 'calendar')}") is not None
        if not is_calendar or not href:
            continue

        name = _text(response, f".//{_tag(_DAV_NS, 'displayname')}") or _resource_id_from_href(href)
        color = _text(response, f".//{_tag(_APPLE_ICAL_NS, 'calendar-color')}")
        description = _text(response, f".//{_tag(_APPLE_ICAL_NS, 'calendar-description')}")
        etag = _text(response, f".//{_tag(_DAV_NS, 'getetag')}")
        ctag = _text(response, f".//{_tag(_CS_NS, 'getctag')}")
        order_str = _text(response, f".//{_tag(_APPLE_ICAL_NS, 'calendar-order')}")
        order = int(order_str) if order_str and order_str.isdigit() else None

        cal_id = _resource_id_from_href(href)
        is_likely_default = (
            "/Default/" in href
            or name.lower() in ("default", "calendar")
        )

        calendars.append(Calendar(
            id=cal_id,
            name=name,
            color=color,
            description=description,
            href=href,
            etag=etag,
            ctag=ctag,
            is_default=False,
        ))

        if is_likely_default:
            default_id = cal_id
        elif default_id is None and order is not None and (best_order is None or order < best_order):
            best_order = order
            default_id = cal_id

    calendars.sort(key=lambda c: c.name.lower())

    # Mark the default calendar
    if default_id:
        for cal in calendars:
            if cal.id == default_id:
                cal.is_default = True
                break
    elif calendars:
        calendars[0].is_default = True

    return calendars


def _parse_events_response(
    xml: str,
    calendar_id: str,
    calendar_name: str | None,
    range_start: str | None,
    range_end: str | None,
) -> list[CalendarEvent]:
    """Parse a REPORT multistatus response into CalendarEvent objects."""
    root = ET.fromstring(xml)
    events: list[CalendarEvent] = []

    for response in root.findall(f".//{_tag(_DAV_NS, 'response')}"):
        href_el = response.find(f"{_tag(_DAV_NS, 'href')}")
        href = href_el.text.strip() if href_el is not None and href_el.text else None

        etag_el = response.find(f".//{_tag(_DAV_NS, 'getetag')}")
        etag = etag_el.text.strip() if etag_el is not None and etag_el.text else None

        data_el = response.find(f".//{_tag(_CALDAV_NS, 'calendar-data')}")
        if data_el is None or not data_el.text:
            continue

        event = parse_ical_event(data_el.text, href, etag, calendar_id, calendar_name)
        if event:
            events.append(event)

    return events


def _text(element: ET.Element, xpath: str) -> str | None:
    """Extract text from the first matching element, or None."""
    el = element.find(xpath)
    if el is not None and el.text:
        return el.text.strip()
    return None


def _resource_id_from_href(href: str) -> str:
    """Extract a resource ID from a CalDAV href."""
    return href.rstrip("/").rsplit("/", 1)[-1].removesuffix(".ics")


def _build_event_href(calendar_href: str, event_id: str) -> str:
    return f"{calendar_href.rstrip('/')}/{event_id}.ics"


def _slugify(name: str) -> str:
    """Create a URL-safe slug from a calendar name."""
    slug: list[str] = []
    prev_dash = False
    for ch in name:
        if ch.isascii() and ch.isalnum():
            slug.append(ch.lower())
            prev_dash = False
        elif not prev_dash:
            slug.append("-")
            prev_dash = True
    result = "".join(slug).strip("-")
    return result if result else "calendar"
