"""XML body builders for CalDAV PROPFIND, REPORT, and MKCALENDAR requests."""

from __future__ import annotations

from xml.etree import ElementTree as ET

# Namespace constants
DAV_NS = "DAV:"
CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
APPLE_ICAL_NS = "http://apple.com/ns/ical/"
CS_NS = "http://calendarserver.org/ns/"

ET.register_namespace("d", DAV_NS)
ET.register_namespace("c", CALDAV_NS)
ET.register_namespace("apple", APPLE_ICAL_NS)
ET.register_namespace("cs", CS_NS)


def _dav(tag: str) -> str:
    return f"{{{DAV_NS}}}{tag}"


def _cal(tag: str) -> str:
    return f"{{{CALDAV_NS}}}{tag}"


def _apple(tag: str) -> str:
    return f"{{{APPLE_ICAL_NS}}}{tag}"


def _cs(tag: str) -> str:
    return f"{{{CS_NS}}}{tag}"


def calendar_home_propfind() -> str:
    """PROPFIND body to discover the calendar-home-set."""
    root = ET.Element(_dav("propfind"))
    prop = ET.SubElement(root, _dav("prop"))
    ET.SubElement(prop, _cal("calendar-home-set"))
    return _tostring(root)


def list_calendars_propfind() -> str:
    """PROPFIND body to list calendars with display name, color, etag, ctag."""
    root = ET.Element(_dav("propfind"))
    prop = ET.SubElement(root, _dav("prop"))
    ET.SubElement(prop, _dav("displayname"))
    ET.SubElement(prop, _dav("resourcetype"))
    ET.SubElement(prop, _dav("getetag"))
    ET.SubElement(prop, _cal("supported-calendar-component-set"))
    ET.SubElement(prop, _apple("calendar-color"))
    ET.SubElement(prop, _apple("calendar-order"))
    ET.SubElement(prop, _apple("calendar-description"))
    ET.SubElement(prop, _cs("getctag"))
    return _tostring(root)


def calendar_query_body(
    start: str | None = None,
    end: str | None = None,
) -> str:
    """REPORT body for calendar-query with optional time-range filter."""
    root = ET.Element(_cal("calendar-query"))
    prop = ET.SubElement(root, _dav("prop"))
    ET.SubElement(prop, _dav("getetag"))
    ET.SubElement(prop, _cal("calendar-data"))

    filter_elem = ET.SubElement(root, _cal("filter"))
    vcalendar = ET.SubElement(filter_elem, _cal("comp-filter"))
    vcalendar.set("name", "VCALENDAR")
    vevent = ET.SubElement(vcalendar, _cal("comp-filter"))
    vevent.set("name", "VEVENT")

    if start and end:
        time_range = ET.SubElement(vevent, _cal("time-range"))
        time_range.set("start", start)
        time_range.set("end", end)

    return _tostring(root)


def uid_report_body(uid: str) -> str:
    """REPORT body for calendar-multiget filtered by UID."""
    root = ET.Element(_cal("calendar-multiget"))
    prop = ET.SubElement(root, _dav("prop"))
    ET.SubElement(prop, _dav("getetag"))
    ET.SubElement(prop, _cal("calendar-data"))

    # Filter by UID
    filter_elem = ET.SubElement(root, _cal("filter"))
    vcalendar = ET.SubElement(filter_elem, _cal("comp-filter"))
    vcalendar.set("name", "VCALENDAR")
    vevent = ET.SubElement(vcalendar, _cal("comp-filter"))
    vevent.set("name", "VEVENT")
    uid_filter = ET.SubElement(vevent, _cal("prop-filter"))
    uid_filter.set("name", "UID")
    text_match = ET.SubElement(uid_filter, _cal("text-match"))
    text_match.set("collation", "i;octet")
    text_match.text = uid

    return _tostring(root)


def mkcalendar_body(name: str, color: str | None = None) -> str:
    """MKCALENDAR body to create a new calendar."""
    root = ET.Element(_cal("mkcalendar"))
    set_elem = ET.SubElement(root, _dav("set"))
    prop = ET.SubElement(set_elem, _dav("prop"))
    dn = ET.SubElement(prop, _dav("displayname"))
    dn.text = name
    if color:
        ac = ET.SubElement(prop, _apple("calendar-color"))
        ac.text = color
    return _tostring(root)


def proppatch_calendar_body(name: str, color: str | None = None) -> str:
    """PROPPATCH body to update calendar display name and/or color."""
    root = ET.Element(_dav("propertyupdate"))
    set_elem = ET.SubElement(root, _dav("set"))
    prop = ET.SubElement(set_elem, _dav("prop"))
    dn = ET.SubElement(prop, _dav("displayname"))
    dn.text = name
    if color:
        ac = ET.SubElement(prop, _apple("calendar-color"))
        ac.text = color
    return _tostring(root)


def _tostring(element: ET.Element) -> str:
    return ET.tostring(element, encoding="unicode", xml_declaration=True)
