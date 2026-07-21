"""CardDAV contact models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContactEmail(BaseModel):
    """An email address entry within a contact."""

    email: str
    label: str | None = None  # e.g. "home", "work"


class ContactPhone(BaseModel):
    """A phone number entry within a contact."""

    number: str
    label: str | None = None


class Contact(BaseModel):
    """A contact (vCard) from a CardDAV address book."""

    id: str = Field(description="UID from the vCard")
    name: str = Field(description="FN (formatted name)")
    emails: list[ContactEmail] = Field(default_factory=list)
    phones: list[ContactPhone] = Field(default_factory=list)
    organization: str | None = None
    title: str | None = None
    notes: str | None = None
    address: str | None = Field(default=None, description="Street address from ADR property")
    href: str | None = Field(default=None, description="CardDAV resource URL")
    etag: str | None = Field(default=None, description="HTTP ETag for optimistic concurrency")


class AddressBook(BaseModel):
    """A CardDAV address book collection."""

    href: str
    name: str


class ContactGroup(BaseModel):
    """A contact group (X-ADDRESSBOOKSERVER-KIND:group vCard)."""

    id: str = Field(description="UID from the vCard")
    name: str = Field(description="FN (display name)")
    member_uids: list[str] = Field(
        default_factory=list,
        description="Member contact UIDs (from X-ADDRESSBOOKSERVER-MEMBER)",
    )
    href: str | None = None
    etag: str | None = None


class ContactCreateResult(BaseModel):
    """Result of creating a contact or group."""

    href: str
    etag: str | None = None
