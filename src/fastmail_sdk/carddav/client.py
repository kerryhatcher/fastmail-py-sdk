"""CardDAV client for Fastmail contacts and contact groups.

Talks CardDAV over HTTP using ``httpx``. Ports the well-tested logic from the
Rust ``fastmail-cli`` tool.
"""

from __future__ import annotations

import asyncio
import logging
from xml.etree import ElementTree as ET

import httpx

from fastmail_sdk.errors import (
    CalDAVServerError,
    FastmailError,
)
from fastmail_sdk.models.contacts import (
    AddressBook,
    Contact,
    ContactCreateResult,
    ContactEmail,
    ContactGroup,
    ContactPhone,
)

logger = logging.getLogger(__name__)

CARDDAV_BASE = "https://carddav.fastmail.com"

# XML namespaces
_DAV_NS = "DAV:"
_CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"


def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


# ------------------------------------------------------------------
# XML helpers
# ------------------------------------------------------------------


def _addressbook_propfind() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:displayname/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>"""


def _addressbook_query() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop>
    <d:getetag/>
    <card:address-data/>
  </d:prop>
</card:addressbook-query>"""


# ------------------------------------------------------------------
# vCard serialization
# ------------------------------------------------------------------


def _escape_vcard(value: str) -> str:
    """Escape special characters for vCard text values."""
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def serialize_vcard(contact: Contact) -> str:
    """Serialize a Contact to vCard 4.0 text."""
    lines = [
        "BEGIN:VCARD",
        "VERSION:4.0",
        f"UID:{contact.id}",
        f"FN:{_escape_vcard(contact.name)}",
    ]

    # Name components
    name_parts = contact.name.split(" ", 1)
    last = name_parts[1] if len(name_parts) > 1 else name_parts[0]
    first = name_parts[0] if len(name_parts) > 1 else ""
    lines.append(f"N:{_escape_vcard(last)};{_escape_vcard(first)};;;")

    for email in contact.emails:
        label = f";TYPE={email.label.upper()}" if email.label else ""
        lines.append(f"EMAIL{label}:{email.email}")

    for phone in contact.phones:
        label = f";TYPE={phone.label.upper()}" if phone.label else ""
        lines.append(f"TEL{label}:{phone.number}")

    if contact.organization:
        lines.append(f"ORG:{_escape_vcard(contact.organization)}")
    if contact.title:
        lines.append(f"TITLE:{_escape_vcard(contact.title)}")
    if contact.notes:
        lines.append(f"NOTE:{_escape_vcard(contact.notes)}")
    if contact.address:
        lines.append(f"ADR:;;{_escape_vcard(contact.address)};;;;")

    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def serialize_group_vcard(group: ContactGroup) -> str:
    """Serialize a ContactGroup to a group vCard."""
    lines = [
        "BEGIN:VCARD",
        "VERSION:4.0",
        f"UID:{group.id}",
        f"FN:{_escape_vcard(group.name)}",
        f"N:{_escape_vcard(group.name)};;;",
        "X-ADDRESSBOOKSERVER-KIND:group",
    ]
    for member_uid in group.member_uids:
        lines.append(f"X-ADDRESSBOOKSERVER-MEMBER:urn:uuid:{member_uid}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


# ------------------------------------------------------------------
# vCard parsing
# ------------------------------------------------------------------


def _unfold_vcard(text: str) -> str:
    """Undo vCard line folding."""
    result: list[str] = []
    for line in text.splitlines():
        if line.startswith(" ") or line.startswith("\t"):
            if result:
                result[-1] += line[1:]
        else:
            result.append(line)
    return "\n".join(result)


def _parse_vcard_value(line: str) -> str:
    """Extract the value portion of a vCard content line."""
    # Handle properties with parameters: "PROP;PARAM=val:actual-value"
    if ":" in line:
        return line.split(":", 1)[1]
    return ""


def _unescape_vcard(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\;", ";").replace("\\,", ",").replace("\\\\", "\\")


def parse_contacts_from_xml(xml: str) -> list[Contact]:
    """Parse contacts from a CardDAV multistatus REPORT response."""
    root = ET.fromstring(xml)
    contacts: list[Contact] = []

    for response in root.findall(f".//{_tag(_DAV_NS, 'response')}"):
        href_el = response.find(f"{_tag(_DAV_NS, 'href')}")
        href = href_el.text.strip() if href_el is not None and href_el.text else None

        etag_el = response.find(f".//{_tag(_DAV_NS, 'getetag')}")
        etag = etag_el.text.strip() if etag_el is not None and etag_el.text else None

        data_el = response.find(f".//{_tag(_CARDDAV_NS, 'address-data')}")
        if data_el is None or not data_el.text:
            continue

        contact = _parse_vcard(data_el.text, href, etag)
        if contact:
            contacts.append(contact)

    return contacts


def parse_groups_from_xml(xml: str) -> list[ContactGroup]:
    """Parse contact groups from a CardDAV multistatus REPORT response."""
    root = ET.fromstring(xml)
    groups: list[ContactGroup] = []

    for response in root.findall(f".//{_tag(_DAV_NS, 'response')}"):
        href_el = response.find(f"{_tag(_DAV_NS, 'href')}")
        href = href_el.text.strip() if href_el is not None and href_el.text else None

        etag_el = response.find(f".//{_tag(_DAV_NS, 'getetag')}")
        etag = etag_el.text.strip() if etag_el is not None and etag_el.text else None

        data_el = response.find(f".//{_tag(_CARDDAV_NS, 'address-data')}")
        if data_el is None or not data_el.text:
            continue

        group = _parse_group_vcard(data_el.text, href, etag)
        if group:
            groups.append(group)

    return groups


def _parse_vcard(text: str, href: str | None, etag: str | None) -> Contact | None:
    """Parse a single vCard into a Contact."""
    unfolded = _unfold_vcard(text)
    contact = Contact(id="", name="")

    for line in unfolded.splitlines():
        line = line.strip()
        if line.startswith("UID:") or line.startswith("UID;"):
            contact.id = _parse_vcard_value(line)
        elif line.startswith("FN:") or line.startswith("FN;"):
            contact.name = _unescape_vcard(_parse_vcard_value(line))
        elif line.startswith("EMAIL"):
            contact.emails.append(
                ContactEmail(
                    email=_parse_vcard_value(line),
                    label=_vcard_param(line, "TYPE"),
                )
            )
        elif line.startswith("TEL"):
            contact.phones.append(
                ContactPhone(
                    number=_parse_vcard_value(line),
                    label=_vcard_param(line, "TYPE"),
                )
            )
        elif line.startswith("ORG:"):
            contact.organization = _unescape_vcard(_parse_vcard_value(line))
        elif line.startswith("TITLE:"):
            contact.title = _unescape_vcard(_parse_vcard_value(line))
        elif line.startswith("NOTE:"):
            contact.notes = _unescape_vcard(_parse_vcard_value(line))
        elif line.startswith("ADR:"):
            # ADR:;;street;;;;
            parts = _parse_vcard_value(line).split(";")
            if len(parts) > 2 and parts[2]:
                contact.address = _unescape_vcard(parts[2])

    if not contact.id or not contact.name:
        return None

    contact.href = href
    contact.etag = etag
    return contact


def _parse_group_vcard(text: str, href: str | None, etag: str | None) -> ContactGroup | None:
    """Parse a group vCard into a ContactGroup."""
    unfolded = _unfold_vcard(text)

    # Only parse group vCards
    if "X-ADDRESSBOOKSERVER-KIND:group" not in unfolded:
        return None

    group = ContactGroup(id="", name="")

    for line in unfolded.splitlines():
        line = line.strip()
        if line.startswith("UID:") or line.startswith("UID;"):
            group.id = _parse_vcard_value(line)
        elif line.startswith("FN:") or line.startswith("FN;"):
            group.name = _unescape_vcard(_parse_vcard_value(line))
        elif line.startswith("X-ADDRESSBOOKSERVER-MEMBER"):
            value = _parse_vcard_value(line)
            # urn:uuid:<uid> → <uid>
            if value.startswith("urn:uuid:"):
                group.member_uids.append(value[9:])

    if not group.id or not group.name:
        return None

    group.href = href
    group.etag = etag
    return group


def _vcard_param(line: str, param: str) -> str | None:
    """Extract a parameter value from a vCard property line."""
    prop = line.split(":", 1)[0] if ":" in line else line
    for part in prop.split(";"):
        if part.upper().startswith(f"{param.upper()}="):
            return part.split("=", 1)[1].lower()
    return None


# ------------------------------------------------------------------
# CardDavClient
# ------------------------------------------------------------------


def _build_contact_href(addressbook_href: str, uid: str) -> str:
    return f"{addressbook_href.rstrip('/')}/{uid}.vcf"


def _build_group_href(addressbook_href: str, uid: str) -> str:
    return f"{addressbook_href.rstrip('/')}/{uid}.vcf"


def _extract_location(headers: httpx.Headers) -> str | None:
    loc = headers.get("Location")
    if loc and loc.startswith(CARDDAV_BASE):
        return loc[len(CARDDAV_BASE) :]
    return loc


class CardDavClient:
    """Async CardDAV client for Fastmail contacts.

    Args:
        username: Fastmail email address.
        app_password: Fastmail app password (not API token).
        base_url: CardDAV base URL.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        username: str,
        app_password: str,
        base_url: str = CARDDAV_BASE,
        timeout: float = 30.0,
    ) -> None:
        self.username = username
        self.app_password = app_password
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> CardDavClient:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            auth=(self.username, self.app_password),
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("CardDavClient not opened — use 'async with client:'")
        return self._client

    # ------------------------------------------------------------------
    # Address books
    # ------------------------------------------------------------------

    async def list_addressbooks(self) -> list[AddressBook]:
        """Discover address books for the user."""
        url = f"{self.base_url}/dav/addressbooks/user/{self.username}/"
        resp = await self.client.request(
            "PROPFIND",
            url,
            content=_addressbook_propfind(),
            headers={"Content-Type": "application/xml", "Depth": "1"},
        )

        if resp.status_code not in (207, 200):
            raise CalDAVServerError(
                f"CardDAV PROPFIND failed: {resp.status_code}",
                resp.status_code,
            )

        return self._parse_addressbooks(resp.text)

    def _parse_addressbooks(self, xml: str) -> list[AddressBook]:
        root = ET.fromstring(xml)
        addressbooks: list[AddressBook] = []

        for response in root.findall(f".//{_tag(_DAV_NS, 'response')}"):
            href_el = response.find(f"{_tag(_DAV_NS, 'href')}")
            href = href_el.text.strip() if href_el is not None and href_el.text else ""

            is_addressbook = response.find(f".//{_tag(_CARDDAV_NS, 'addressbook')}") is not None
            if not is_addressbook or not href:
                continue

            # Skip the parent collection itself
            if href.endswith(f"{self.username}/"):
                continue

            name_el = response.find(f".//{_tag(_DAV_NS, 'displayname')}")
            name = (
                name_el.text.strip()
                if name_el is not None and name_el.text
                else (href.rstrip("/").rsplit("/", 1)[-1])
            )

            addressbooks.append(AddressBook(href=href, name=name))

        return addressbooks

    async def default_addressbook_href(self) -> str:
        """Return the first address book href."""
        books = await self.list_addressbooks()
        if not books:
            raise FastmailError("No CardDAV address books found")
        return books[0].href

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def list_contacts(self, addressbook_href: str) -> list[Contact]:
        """List all contacts in an address book."""
        url = f"{self.base_url}{addressbook_href}"
        resp = await self.client.request(
            "REPORT",
            url,
            content=_addressbook_query(),
            headers={"Content-Type": "application/xml", "Depth": "1"},
        )

        if resp.status_code not in (207, 200):
            raise CalDAVServerError(
                f"CardDAV REPORT failed: {resp.status_code}",
                resp.status_code,
            )

        return parse_contacts_from_xml(resp.text)

    async def search_contacts(self, query: str) -> list[Contact]:
        """Search contacts by name, email, or organization across all address books."""
        addressbooks = await self.list_addressbooks()
        query_lower = query.lower()

        async def fetch(ab: AddressBook) -> list[Contact]:
            try:
                return await self.list_contacts(ab.href)
            except Exception:
                logger.warning("Failed to fetch contacts from %s", ab.href, exc_info=True)
                return []

        results = await asyncio.gather(*(fetch(ab) for ab in addressbooks))
        all_contacts: list[Contact] = []
        for contacts in results:
            all_contacts.extend(contacts)

        return [
            c
            for c in all_contacts
            if query_lower in c.name.lower()
            or any(query_lower in e.email.lower() for e in c.emails)
            or (c.organization and query_lower in c.organization.lower())
        ]

    async def get_contact_by_id(self, contact_id: str) -> Contact:
        """Find a contact by exact ID across all address books."""
        addressbooks = await self.list_addressbooks()
        for ab in addressbooks:
            contacts = await self.list_contacts(ab.href)
            for contact in contacts:
                if contact.id == contact_id:
                    return contact
        raise FastmailError(f"Contact not found: {contact_id}")

    async def create_contact(
        self,
        addressbook_href: str,
        contact: Contact,
    ) -> ContactCreateResult:
        """Create a new contact."""
        href = _build_contact_href(addressbook_href, contact.id)
        url = f"{self.base_url}{href}"
        vcard = serialize_vcard(contact)

        resp = await self.client.put(
            url,
            content=vcard,
            headers={
                "Content-Type": "text/vcard; charset=utf-8",
                "If-None-Match": "*",
            },
        )

        if resp.status_code not in (201, 204, 200):
            if resp.status_code == 412:
                raise FastmailError(f"Contact conflict for '{contact.id}'")
            raise CalDAVServerError(
                f"Contact PUT failed: {resp.status_code}",
                resp.status_code,
            )

        return ContactCreateResult(
            href=_extract_location(resp.headers) or href,
            etag=resp.headers.get("ETag"),
        )

    async def update_contact(
        self,
        href: str,
        etag: str,
        contact: Contact,
    ) -> str:
        """Update an existing contact. Returns the new ETag."""
        url = f"{self.base_url}{href}"
        vcard = serialize_vcard(contact)

        resp = await self.client.put(
            url,
            content=vcard,
            headers={
                "Content-Type": "text/vcard; charset=utf-8",
                "If-Match": etag,
            },
        )

        if resp.status_code not in (200, 204, 201):
            if resp.status_code == 412:
                raise FastmailError(
                    f"Contact conflict for '{contact.id}': "
                    f"sent ETag '{etag}', server has '{resp.headers.get('ETag')}'"
                )
            if resp.status_code == 404:
                raise FastmailError(f"Contact not found: {contact.id}")
            raise CalDAVServerError(
                f"Contact PUT failed: {resp.status_code}",
                resp.status_code,
            )

        return resp.headers.get("ETag") or etag

    async def delete_contact(self, href: str, etag: str, contact_id: str) -> None:
        """Delete a contact."""
        url = f"{self.base_url}{href}"
        resp = await self.client.delete(url, headers={"If-Match": etag})

        if resp.status_code in (204, 200, 202):
            return

        if resp.status_code == 412:
            raise FastmailError(
                f"Contact conflict for '{contact_id}': "
                f"sent ETag '{etag}', server has '{resp.headers.get('ETag')}'"
            )
        if resp.status_code == 404:
            raise FastmailError(f"Contact not found: {contact_id}")
        raise CalDAVServerError(
            f"Contact DELETE failed: {resp.status_code}",
            resp.status_code,
        )

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def list_groups(self) -> list[ContactGroup]:
        """List all contact groups across all address books."""
        addressbooks = await self.list_addressbooks()

        async def fetch(ab: AddressBook) -> list[ContactGroup]:
            try:
                url = f"{self.base_url}{ab.href}"
                resp = await self.client.request(
                    "REPORT",
                    url,
                    content=_addressbook_query(),
                    headers={"Content-Type": "application/xml", "Depth": "1"},
                )
                if resp.status_code not in (207, 200):
                    logger.warning("REPORT failed for %s: %s", ab.href, resp.status_code)
                    return []
                return parse_groups_from_xml(resp.text)
            except Exception:
                logger.warning("Failed to fetch groups from %s", ab.href, exc_info=True)
                return []

        results = await asyncio.gather(*(fetch(ab) for ab in addressbooks))
        all_groups: list[ContactGroup] = []
        for groups in results:
            all_groups.extend(groups)
        return all_groups

    async def get_group_by_id(self, group_id: str) -> ContactGroup:
        """Find a group by exact ID."""
        groups = await self.list_groups()
        for g in groups:
            if g.id == group_id:
                return g
        raise FastmailError(f"Group not found: {group_id}")

    async def get_group_by_name(self, name: str) -> ContactGroup:
        """Find a group by exact name (case-sensitive)."""
        groups = await self.list_groups()
        matches = [g for g in groups if g.name == name]
        if not matches:
            raise FastmailError(f"Group not found: {name}")
        if len(matches) > 1:
            raise FastmailError(
                f"Ambiguous group name '{name}': multiple groups match. Use group ID instead."
            )
        return matches[0]

    async def create_group(
        self,
        addressbook_href: str,
        group: ContactGroup,
    ) -> ContactCreateResult:
        """Create a new contact group."""
        href = _build_group_href(addressbook_href, group.id)
        url = f"{self.base_url}{href}"
        vcard = serialize_group_vcard(group)

        resp = await self.client.put(
            url,
            content=vcard,
            headers={
                "Content-Type": "text/vcard; charset=utf-8",
                "If-None-Match": "*",
            },
        )

        if resp.status_code not in (201, 204, 200):
            if resp.status_code == 412:
                raise FastmailError(f"Group conflict for '{group.id}'")
            raise CalDAVServerError(
                f"Group PUT failed: {resp.status_code}",
                resp.status_code,
            )

        return ContactCreateResult(
            href=_extract_location(resp.headers) or href,
            etag=resp.headers.get("ETag"),
        )

    async def rename_group(
        self,
        href: str,
        etag: str,
        group: ContactGroup,
        new_name: str,
    ) -> str:
        """Rename a contact group. Returns the new ETag."""
        updated = ContactGroup(
            id=group.id,
            name=new_name,
            member_uids=list(group.member_uids),
            href=group.href,
            etag=group.etag,
        )
        url = f"{self.base_url}{href}"
        vcard = serialize_group_vcard(updated)

        resp = await self.client.put(
            url,
            content=vcard,
            headers={
                "Content-Type": "text/vcard; charset=utf-8",
                "If-Match": etag,
            },
        )

        if resp.status_code not in (200, 204, 201):
            if resp.status_code == 412:
                raise FastmailError(
                    f"Group conflict for '{group.id}': "
                    f"sent ETag '{etag}', server has '{resp.headers.get('ETag')}'"
                )
            raise CalDAVServerError(
                f"Group PUT failed: {resp.status_code}",
                resp.status_code,
            )

        return resp.headers.get("ETag") or etag

    async def delete_group(self, href: str, etag: str, group_id: str) -> None:
        """Delete a contact group."""
        url = f"{self.base_url}{href}"
        resp = await self.client.delete(url, headers={"If-Match": etag})

        if resp.status_code in (204, 200, 202):
            return

        if resp.status_code == 412:
            raise FastmailError(
                f"Group conflict for '{group_id}': "
                f"sent ETag '{etag}', server has '{resp.headers.get('ETag')}'"
            )
        if resp.status_code == 404:
            raise FastmailError(f"Group not found: {group_id}")
        raise CalDAVServerError(
            f"Group DELETE failed: {resp.status_code}",
            resp.status_code,
        )

    async def add_group_member(
        self,
        href: str,
        etag: str,
        group: ContactGroup,
        contact_id: str,
    ) -> str:
        """Add a contact to a group. Returns the new ETag."""
        if contact_id in group.member_uids:
            return etag  # Already a member

        updated = ContactGroup(
            id=group.id,
            name=group.name,
            member_uids=list(group.member_uids) + [contact_id],
            href=group.href,
            etag=group.etag,
        )
        return await self.rename_group(href, etag, updated, group.name)

    async def remove_group_member(
        self,
        href: str,
        etag: str,
        group: ContactGroup,
        contact_id: str,
    ) -> str:
        """Remove a contact from a group. Returns the new ETag."""
        if contact_id not in group.member_uids:
            return etag  # Not a member

        updated = ContactGroup(
            id=group.id,
            name=group.name,
            member_uids=[uid for uid in group.member_uids if uid != contact_id],
            href=group.href,
            etag=group.etag,
        )
        return await self.rename_group(href, etag, updated, group.name)
