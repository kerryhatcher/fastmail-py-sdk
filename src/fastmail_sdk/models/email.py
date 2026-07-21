"""JMAP email models — sessions, mailboxes, emails, identities, masked emails.

All models use ``camelCase`` field aliases so they round-trip through the
Fastmail JMAP API without manual key translation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Account(BaseModel):
    """A JMAP account record."""

    name: str
    is_personal: bool = Field(alias="isPersonal")
    is_read_only: bool = Field(alias="isReadOnly")
    account_capabilities: dict[str, Any] = Field(default_factory=dict, alias="accountCapabilities")


class Session(BaseModel):
    """A JMAP session, obtained by GETting the session endpoint."""

    capabilities: dict[str, Any] = Field(default_factory=dict)
    accounts: dict[str, Account] = Field(default_factory=dict)
    primary_accounts: dict[str, str] = Field(default_factory=dict, alias="primaryAccounts")
    username: str
    api_url: str = Field(alias="apiUrl")
    download_url: str = Field(alias="downloadUrl")
    upload_url: str = Field(alias="uploadUrl")
    event_source_url: str | None = Field(default=None, alias="eventSourceUrl")
    state: str | None = None

    def primary_account_id(self) -> str | None:
        """Return the primary mail account ID, if any."""
        return self.primary_accounts.get("urn:ietf:params:jmap:mail")


class EmailAddress(BaseModel):
    """An email address with optional display name."""

    name: str | None = None
    email: str


class Mailbox(BaseModel):
    """A mailbox (folder) in the user's account."""

    id: str
    name: str
    parent_id: str | None = Field(default=None, alias="parentId")
    role: str | None = None
    total_emails: int = Field(default=0, alias="totalEmails")
    unread_emails: int = Field(default=0, alias="unreadEmails")
    total_threads: int = Field(default=0, alias="totalThreads")
    unread_threads: int = Field(default=0, alias="unreadThreads")
    sort_order: int = Field(default=0, alias="sortOrder")


class EmailBodyPart(BaseModel):
    """A body part or attachment within an email."""

    part_id: str | None = Field(default=None, alias="partId")
    blob_id: str | None = Field(default=None, alias="blobId")
    size: int = 0
    name: str | None = None
    content_type: str | None = Field(default=None, alias="type")
    charset: str | None = None
    disposition: str | None = None
    cid: str | None = None


class EmailBodyValue(BaseModel):
    """The decoded text content of a body part."""

    value: str
    is_encoding_problem: bool = Field(default=False, alias="isEncodingProblem")
    is_truncated: bool = Field(default=False, alias="isTruncated")


class Email(BaseModel):
    """A full email object from the JMAP API."""

    id: str
    blob_id: str | None = Field(default=None, alias="blobId")
    thread_id: str | None = Field(default=None, alias="threadId")
    mailbox_ids: dict[str, bool] = Field(default_factory=dict, alias="mailboxIds")
    keywords: dict[str, bool] = Field(default_factory=dict)
    size: int = 0
    received_at: str | None = Field(default=None, alias="receivedAt")
    message_id: list[str] | None = Field(default=None, alias="messageId")
    in_reply_to: list[str] | None = Field(default=None, alias="inReplyTo")
    references: list[str] | None = None
    from_: list[EmailAddress] | None = Field(default=None, alias="from")
    to: list[EmailAddress] | None = None
    cc: list[EmailAddress] | None = None
    bcc: list[EmailAddress] | None = None
    reply_to: list[EmailAddress] | None = Field(default=None, alias="replyTo")
    subject: str | None = None
    sent_at: str | None = Field(default=None, alias="sentAt")
    preview: str | None = None
    has_attachment: bool = Field(default=False, alias="hasAttachment")
    text_body: list[EmailBodyPart] | None = Field(default=None, alias="textBody")
    html_body: list[EmailBodyPart] | None = Field(default=None, alias="htmlBody")
    attachments: list[EmailBodyPart] | None = None
    body_values: dict[str, EmailBodyValue] | None = Field(default=None, alias="bodyValues")

    def is_unread(self) -> bool:
        return "$seen" not in self.keywords

    def is_flagged(self) -> bool:
        return "$flagged" in self.keywords

    def is_draft(self) -> bool:
        return "$draft" in self.keywords

    def text_content(self) -> str | None:
        """Return the plain-text body, or None."""
        if self.body_values is None or self.text_body is None:
            return None
        for part in self.text_body:
            if part.part_id and part.part_id in self.body_values:
                return self.body_values[part.part_id].value
        return None

    def html_content(self) -> str | None:
        """Return the HTML body, or None."""
        if self.body_values is None or self.html_body is None:
            return None
        for part in self.html_body:
            if part.part_id and part.part_id in self.body_values:
                return self.body_values[part.part_id].value
        return None


class Identity(BaseModel):
    """A sender identity (From address) in the user's account."""

    id: str
    name: str
    email: str
    reply_to: list[EmailAddress] | None = Field(default=None, alias="replyTo")
    bcc: list[EmailAddress] | None = None
    text_signature: str | None = Field(default=None, alias="textSignature")
    html_signature: str | None = Field(default=None, alias="htmlSignature")
    may_delete: bool = Field(default=False, alias="mayDelete")


class MaskedEmail(BaseModel):
    """A masked (disposable) email address."""

    id: str
    email: str
    state: str | None = None  # pending, enabled, disabled, deleted
    for_domain: str | None = Field(default=None, alias="forDomain")
    description: str | None = None
    last_message_at: str | None = Field(default=None, alias="lastMessageAt")
    created_at: str | None = Field(default=None, alias="createdAt")
    created_by: str | None = Field(default=None, alias="createdBy")
    url: str | None = None


class SearchFilter(BaseModel):
    """JMAP email search filter — all fields optional and ANDed together."""

    text: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    subject: str | None = None
    body: str | None = None
    mailbox: str | None = None
    has_attachment: bool = False
    min_size: int | None = None
    max_size: int | None = None
    before: str | None = None
    after: str | None = None
    unread: bool = False
    flagged: bool = False
