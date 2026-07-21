"""JMAP client for Fastmail email — mailboxes, emails, search, send, masked email.

Talks JMAP over HTTP using ``httpx``. Ports the well-tested logic from the
Rust ``fastmail-cli`` tool.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from fastmail_sdk.errors import FastmailError
from fastmail_sdk.models.email import (
    Email,
    EmailAddress,
    Identity,
    Mailbox,
    MaskedEmail,
    SearchFilter,
    Session,
)

logger = logging.getLogger(__name__)

SESSION_URL = "https://api.fastmail.com/jmap/session"

DESIRED_CAPABILITIES = [
    "urn:ietf:params:jmap:core",
    "urn:ietf:params:jmap:mail",
    "urn:ietf:params:jmap:submission",
    "https://www.fastmail.com/dev/maskedemail",
]


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


@dataclass
class _ComposeContext:
    account_id: str
    mailbox: Mailbox
    identity: Identity | None
    draft: bool


@dataclass
class _EmailDraft:
    to: list[EmailAddress]
    cc: list[EmailAddress]
    bcc: list[EmailAddress]
    subject: str
    body: str
    in_reply_to: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)


def _addrs_json(addrs: list[EmailAddress]) -> list[dict[str, str | None]]:
    return [{"email": a.email, "name": a.name} for a in addrs]


def _pick_identity(identities: list[Identity], from_email: str | None) -> Identity:
    if from_email:
        for ident in identities:
            if ident.email.lower() == from_email.lower():
                return ident
        raise FastmailError(
            f"No identity found matching '{from_email}'. "
            "Run `list identities` to see available identities."
        )
    if not identities:
        raise FastmailError("Identity not found for sending")
    return identities[0]


# ------------------------------------------------------------------
# JmapClient
# ------------------------------------------------------------------


class JmapClient:
    """Async JMAP client for Fastmail.

    Args:
        token: Fastmail API token (from Settings > Privacy & Security > API tokens).
        session_url: JMAP session endpoint URL.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        token: str,
        session_url: str = SESSION_URL,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._session_url = session_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._session: Session | None = None
        self._available_capabilities: list[str] = []
        self._cached_mailboxes: list[Mailbox] | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> JmapClient:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout),
            headers={"Authorization": f"Bearer {self._token}"},
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> Session:
        """Fetch the JMAP session and cache it."""
        if self._client is None:
            raise RuntimeError("JmapClient not opened — use 'async with client:'")

        logger.debug("Fetching JMAP session from %s", self._session_url)
        resp = await self._client.get(self._session_url)

        if resp.status_code == 401:
            raise FastmailError("Authentication failed — invalid API token")
        if resp.status_code == 429:
            raise FastmailError("Rate limited. Try again later.")
        if resp.status_code >= 500:
            raise FastmailError(f"Server error: {resp.status_code}")
        if 400 <= resp.status_code < 500:
            raise FastmailError(f"HTTP {resp.status_code} from API")

        data = resp.json()
        self._session = Session.model_validate(data)
        self._available_capabilities = [
            cap for cap in DESIRED_CAPABILITIES if cap in self._session.capabilities
        ]
        logger.debug("Session established for %s", self._session.username)
        return self._session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _account_id(self) -> str:
        if self._session is None:
            raise FastmailError("Not authenticated — call authenticate() first")
        aid = self._session.primary_account_id()
        if aid is None:
            raise FastmailError("No primary mail account found")
        return aid

    def _require_capability(self, capability: str, action: str) -> None:
        if self._session is None:
            raise FastmailError("Not authenticated — call authenticate() first")
        if capability not in self._session.capabilities:
            raise FastmailError(
                f"{action} requires the '{capability}' capability. "
                "Your API token may be read-only. Generate a new token with "
                "appropriate permissions at Fastmail Settings > Privacy & Security > API tokens."
            )

    async def _request(self, method_calls: list[list[Any]]) -> list[list[Any]]:
        """POST a batch of JMAP method calls and return the method responses."""
        if self._client is None or self._session is None:
            raise FastmailError("Not authenticated — call authenticate() first")

        body = {
            "using": self._available_capabilities,
            "methodCalls": method_calls,
        }
        resp = await self._client.post(self._session.api_url, json=body)

        if resp.status_code == 401:
            raise FastmailError("Token expired or invalid")
        if resp.status_code == 429:
            raise FastmailError("Rate limited. Try again later.")
        if resp.status_code >= 500:
            raise FastmailError(f"Server error: {resp.status_code}")
        if 400 <= resp.status_code < 500:
            raise FastmailError(f"HTTP {resp.status_code} from API")

        data = resp.json()
        return data.get("methodResponses", [])

    @staticmethod
    def _parse_response(response: list[Any], expected_method: str) -> Any:
        """Extract data from a JMAP method response array.

        Response format: ``[methodName, data, callId]`` or
        ``["error", {type, description}, callId]``.
        """
        if not isinstance(response, list) or len(response) < 2:
            raise FastmailError(f"Invalid {expected_method} response: not an array")

        method_name = str(response[0]) if response[0] else ""

        if method_name == "error":
            err = response[1] if len(response) > 1 else {}
            err_type = err.get("type", "unknown") if isinstance(err, dict) else "unknown"
            desc = err.get("description", "No description") if isinstance(err, dict) else str(err)
            raise FastmailError(f"JMAP {expected_method} error: {err_type} — {desc}")

        if len(response) < 2:
            raise FastmailError(f"Missing {expected_method} response data")
        return response[1]

    # ------------------------------------------------------------------
    # Mailboxes
    # ------------------------------------------------------------------

    async def list_mailboxes(self) -> list[Mailbox]:
        """List all mailboxes, with caching after the first call."""
        if self._cached_mailboxes is not None:
            return self._cached_mailboxes

        account_id = self._account_id()
        responses = await self._request([
            [
                "Mailbox/get",
                {
                    "accountId": account_id,
                    "properties": [
                        "id", "name", "parentId", "role",
                        "totalEmails", "unreadEmails",
                        "totalThreads", "unreadThreads", "sortOrder",
                    ],
                },
                "m0",
            ]
        ])

        data = self._parse_response(responses[0], "Mailbox/get")
        mailboxes = [Mailbox.model_validate(m) for m in data.get("list", [])]
        self._cached_mailboxes = mailboxes
        return mailboxes

    async def find_mailbox(self, name: str) -> Mailbox:
        """Look up a mailbox by name or role (case-insensitive)."""
        mailboxes = await self.list_mailboxes()
        name_lower = name.lower()

        for m in mailboxes:
            if m.name.lower() == name_lower:
                return m

        for m in mailboxes:
            if m.role and m.role.lower() == name_lower:
                return m

        raise FastmailError(f"Mailbox not found: {name}")

    # ------------------------------------------------------------------
    # Emails
    # ------------------------------------------------------------------

    async def list_emails(self, mailbox_id: str, limit: int = 50) -> list[Email]:
        """List emails in a mailbox, newest first."""
        account_id = self._account_id()
        responses = await self._request([
            [
                "Email/query",
                {
                    "accountId": account_id,
                    "filter": {"inMailbox": mailbox_id},
                    "sort": [{"property": "receivedAt", "isAscending": False}],
                    "limit": limit,
                },
                "q0",
            ],
            [
                "Email/get",
                {
                    "accountId": account_id,
                    "#ids": {
                        "resultOf": "q0",
                        "name": "Email/query",
                        "path": "/ids",
                    },
                    "properties": [
                        "id", "threadId", "mailboxIds", "keywords",
                        "size", "receivedAt", "from", "to", "cc",
                        "subject", "preview", "hasAttachment",
                    ],
                },
                "g0",
            ],
        ])

        data = self._parse_response(responses[1], "Email/get")
        return [Email.model_validate(e) for e in data.get("list", [])]

    async def get_email(self, email_id: str) -> Email:
        """Fetch a single email with full content."""
        account_id = self._account_id()
        responses = await self._request([
            [
                "Email/get",
                {
                    "accountId": account_id,
                    "ids": [email_id],
                    "properties": [
                        "id", "blobId", "threadId", "mailboxIds", "keywords",
                        "size", "receivedAt", "messageId", "inReplyTo", "references",
                        "from", "to", "cc", "bcc", "replyTo", "subject", "sentAt",
                        "preview", "hasAttachment", "textBody", "htmlBody", "attachments",
                        "bodyValues",
                    ],
                    "fetchTextBodyValues": True,
                    "fetchHTMLBodyValues": True,
                },
                "g0",
            ]
        ])

        data = self._parse_response(responses[0], "Email/get")
        not_found = data.get("notFound", [])
        if not_found:
            raise FastmailError(f"Email not found: {email_id}")

        emails = [Email.model_validate(e) for e in data.get("list", [])]
        if not emails:
            raise FastmailError(f"Email not found: {email_id}")
        return emails[0]

    async def get_thread(self, email_id: str) -> list[Email]:
        """Fetch all emails in the same thread as the given email."""
        email = await self.get_email(email_id)
        if not email.thread_id:
            raise FastmailError("Email has no thread ID")

        account_id = self._account_id()

        # Get thread to find all email IDs
        responses = await self._request([
            ["Thread/get", {"accountId": account_id, "ids": [email.thread_id]}, "t0"]
        ])
        data = self._parse_response(responses[0], "Thread/get")
        threads = data.get("list", [])
        if not threads:
            raise FastmailError("Thread not found")
        email_ids = threads[0].get("emailIds", [])

        # Fetch all emails in the thread
        responses = await self._request([
            [
                "Email/get",
                {
                    "accountId": account_id,
                    "ids": email_ids,
                    "properties": [
                        "id", "threadId", "mailboxIds", "keywords",
                        "size", "receivedAt", "from", "to", "cc",
                        "subject", "preview", "hasAttachment",
                        "textBody", "htmlBody", "bodyValues",
                    ],
                    "fetchTextBodyValues": True,
                    "fetchHTMLBodyValues": True,
                },
                "e0",
            ]
        ])
        data = self._parse_response(responses[0], "Email/get")
        return [Email.model_validate(e) for e in data.get("list", [])]

    async def search_emails(
        self,
        filter: SearchFilter,
        mailbox_id: str | None = None,
        limit: int = 50,
    ) -> list[Email]:
        """Search emails with full JMAP filter support."""
        account_id = self._account_id()

        jmap_filter: dict[str, Any] = {}
        if filter.text:
            jmap_filter["text"] = filter.text
        if filter.from_:
            jmap_filter["from"] = filter.from_
        if filter.to:
            jmap_filter["to"] = filter.to
        if filter.cc:
            jmap_filter["cc"] = filter.cc
        if filter.bcc:
            jmap_filter["bcc"] = filter.bcc
        if filter.subject:
            jmap_filter["subject"] = filter.subject
        if filter.body:
            jmap_filter["body"] = filter.body
        if mailbox_id:
            jmap_filter["inMailbox"] = mailbox_id
        if filter.has_attachment:
            jmap_filter["hasAttachment"] = True
        if filter.min_size is not None:
            jmap_filter["minSize"] = filter.min_size
        if filter.max_size is not None:
            jmap_filter["maxSize"] = filter.max_size
        if filter.before:
            date = filter.before if "T" in filter.before else f"{filter.before}T00:00:00Z"
            jmap_filter["before"] = date
        if filter.after:
            date = filter.after if "T" in filter.after else f"{filter.after}T00:00:00Z"
            jmap_filter["after"] = date
        if filter.unread:
            jmap_filter["notKeyword"] = "$seen"
        if filter.flagged:
            jmap_filter["hasKeyword"] = "$flagged"

        responses = await self._request([
            [
                "Email/query",
                {
                    "accountId": account_id,
                    "filter": jmap_filter,
                    "sort": [{"property": "receivedAt", "isAscending": False}],
                    "limit": limit,
                },
                "q0",
            ],
            [
                "Email/get",
                {
                    "accountId": account_id,
                    "#ids": {
                        "resultOf": "q0",
                        "name": "Email/query",
                        "path": "/ids",
                    },
                    "properties": [
                        "id", "threadId", "mailboxIds", "keywords",
                        "size", "receivedAt", "from", "to", "cc",
                        "subject", "preview", "hasAttachment",
                    ],
                },
                "g0",
            ],
        ])

        data = self._parse_response(responses[1], "Email/get")
        return [Email.model_validate(e) for e in data.get("list", [])]

    # ------------------------------------------------------------------
    # Identities
    # ------------------------------------------------------------------

    async def list_identities(self) -> list[Identity]:
        """List sender identities."""
        account_id = self._account_id()
        responses = await self._request([
            ["Identity/get", {"accountId": account_id}, "i0"]
        ])
        data = self._parse_response(responses[0], "Identity/get")
        return [Identity.model_validate(i) for i in data.get("list", [])]

    async def _resolve_identity(self, from_email: str | None) -> Identity:
        identities = await self.list_identities()
        return _pick_identity(identities, from_email)

    # ------------------------------------------------------------------
    # Compose helpers
    # ------------------------------------------------------------------

    async def _prepare_compose(
        self, from_email: str | None, draft: bool,
    ) -> _ComposeContext:
        if not draft:
            self._require_capability("urn:ietf:params:jmap:submission", "Email sending")

        account_id = self._account_id()
        mailbox = await self.find_mailbox("drafts" if draft else "sent")

        identity = None
        try:
            identity = await self._resolve_identity(from_email)
        except FastmailError:
            if not draft:
                raise

        return _ComposeContext(account_id, mailbox, identity, draft)

    async def _create_and_submit_email(
        self, ctx: _ComposeContext, draft: _EmailDraft,
    ) -> str:
        email_create: dict[str, Any] = {
            "mailboxIds": {ctx.mailbox.id: True},
        }
        if ctx.draft:
            email_create["keywords"] = {"$draft": True, "$seen": True}
        if ctx.identity:
            email_create["from"] = [{"email": ctx.identity.email, "name": ctx.identity.name}]

        email_create["to"] = _addrs_json(draft.to)
        if draft.cc:
            email_create["cc"] = _addrs_json(draft.cc)
        if draft.bcc:
            email_create["bcc"] = _addrs_json(draft.bcc)
        email_create["subject"] = draft.subject
        email_create["bodyValues"] = {
            "body": {"value": draft.body, "charset": "utf-8"},
        }
        email_create["textBody"] = [{"partId": "body", "type": "text/plain"}]
        if draft.in_reply_to:
            email_create["inReplyTo"] = draft.in_reply_to
        if draft.references:
            email_create["references"] = draft.references

        method_calls: list[list[Any]] = [
            [
                "Email/set",
                {"accountId": ctx.account_id, "create": {"email": email_create}},
                "e0",
            ],
        ]
        if not ctx.draft and ctx.identity:
            method_calls.append([
                "EmailSubmission/set",
                {
                    "accountId": ctx.account_id,
                    "create": {
                        "submission": {
                            "identityId": ctx.identity.id,
                            "emailId": "#email",
                        }
                    },
                    "onSuccessUpdateEmail": {
                        "#submission": {"keywords/$seen": True},
                    },
                },
                "s0",
            ])

        responses = await self._request(method_calls)
        return self._parse_email_create_response(responses)

    @staticmethod
    def _parse_email_create_response(responses: list[list[Any]]) -> str:
        if not responses:
            raise FastmailError("Email/set: no response")

        data = JmapClient._parse_response(responses[0], "Email/set")
        not_created = data.get("notCreated", {})
        if not_created and "email" in not_created:
            err = not_created["email"]
            raise FastmailError(
                f"Email/set failed: {err.get('type', 'unknown')} — "
                f"{err.get('description', 'Failed to create email')}"
            )

        created = data.get("created", {})
        email_obj = created.get("email", {})
        email_id = email_obj.get("id")
        if not email_id:
            raise FastmailError("Email/set: no email ID returned")

        # Check EmailSubmission/set response if present
        if len(responses) > 1:
            sub_data = JmapClient._parse_response(responses[1], "EmailSubmission/set")
            sub_not_created = sub_data.get("notCreated", {})
            if sub_not_created and "submission" in sub_not_created:
                err = sub_not_created["submission"]
                raise FastmailError(
                    f"EmailSubmission/set failed: {err.get('type', 'unknown')} — "
                    f"{err.get('description', 'Email created but submission failed')}"
                )

        return str(email_id)

    # ------------------------------------------------------------------
    # Send / Reply / Forward
    # ------------------------------------------------------------------

    async def send_email(
        self,
        to: list[EmailAddress],
        subject: str,
        body: str,
        *,
        cc: list[EmailAddress] | None = None,
        bcc: list[EmailAddress] | None = None,
        from_email: str | None = None,
        in_reply_to: str | None = None,
        draft: bool = False,
    ) -> str:
        """Send (or draft) a new email. Returns the email ID."""
        ctx = await self._prepare_compose(from_email, draft)
        return await self._create_and_submit_email(
            ctx,
            _EmailDraft(
                to=to,
                cc=cc or [],
                bcc=bcc or [],
                subject=subject,
                body=body,
                in_reply_to=[in_reply_to] if in_reply_to else [],
            ),
        )

    async def reply_email(
        self,
        original: Email,
        body: str,
        *,
        reply_all: bool = False,
        cc: list[EmailAddress] | None = None,
        bcc: list[EmailAddress] | None = None,
        from_email: str | None = None,
        draft: bool = False,
    ) -> str:
        """Reply to an email. Returns the new email ID."""
        ctx = await self._prepare_compose(from_email, draft)

        my_email = (
            ctx.identity.email.lower()
            if ctx.identity
            else (from_email.lower() if from_email else "")
        )

        # Build To recipients
        to_addrs: list[EmailAddress] = list(original.from_ or [])
        if reply_all and original.to:
            for addr in original.to:
                if not my_email or addr.email.lower() != my_email:
                    to_addrs.append(addr)

        # Build CC recipients
        cc_addrs = list(cc or [])
        if reply_all and original.cc:
            for addr in original.cc:
                if not my_email or addr.email.lower() != my_email:
                    cc_addrs.append(addr)

        # Build subject
        subj = original.subject or ""
        if not subj.lower().startswith("re:"):
            subj = f"Re: {subj}"

        # Build references
        references = list(original.references or [])
        if original.message_id:
            for mid in original.message_id:
                if mid not in references:
                    references.append(mid)

        return await self._create_and_submit_email(
            ctx,
            _EmailDraft(
                to=to_addrs,
                cc=cc_addrs,
                bcc=bcc or [],
                subject=subj,
                body=body,
                in_reply_to=original.message_id or [],
                references=references,
            ),
        )

    async def forward_email(
        self,
        original: Email,
        to: list[EmailAddress],
        body: str,
        *,
        cc: list[EmailAddress] | None = None,
        bcc: list[EmailAddress] | None = None,
        from_email: str | None = None,
        draft: bool = False,
    ) -> str:
        """Forward an email. Returns the new email ID."""
        ctx = await self._prepare_compose(from_email, draft)

        # Build subject
        subj = original.subject or ""
        if not subj.lower().startswith("fwd:"):
            subj = f"Fwd: {subj}"

        # Build forwarded body with attribution
        original_body = original.text_content() or ""
        sender = ""
        if original.from_:
            first = original.from_[0]
            sender = f"{first.name} <{first.email}>" if first.name else first.email
        date = original.received_at or "unknown date"
        orig_subj = original.subject or ""

        full_body = (
            f"{body}\n\n"
            f"---------- Forwarded message ---------\n"
            f"From: {sender}\n"
            f"Date: {date}\n"
            f"Subject: {orig_subj}\n"
            f"\n{original_body}"
        )

        return await self._create_and_submit_email(
            ctx,
            _EmailDraft(
                to=to,
                cc=cc or [],
                bcc=bcc or [],
                subject=subj,
                body=full_body,
            ),
        )

    # ------------------------------------------------------------------
    # Move / Mark
    # ------------------------------------------------------------------

    async def move_email(self, email_id: str, mailbox_id: str) -> None:
        """Move an email to a different mailbox."""
        account_id = self._account_id()
        responses = await self._request([
            [
                "Email/set",
                {
                    "accountId": account_id,
                    "update": {email_id: {"mailboxIds": {mailbox_id: True}}},
                },
                "m0",
            ]
        ])

        data = self._parse_response(responses[0], "Email/set")
        not_updated = data.get("notUpdated", {})
        if not_updated and email_id in not_updated:
            err = not_updated[email_id]
            raise FastmailError(
                f"Email/set move failed: {err.get('type', 'unknown')} — "
                f"{err.get('description', 'Failed to move email')}"
            )

    async def mark_spam(self, email_id: str) -> None:
        """Move an email to the Junk mailbox."""
        junk = await self.find_mailbox("junk")
        await self.move_email(email_id, junk.id)

    async def set_keywords(self, email_id: str, keywords: dict[str, bool]) -> None:
        """Update keywords on an email (e.g. ``{"$seen": True}`` to mark read)."""
        account_id = self._account_id()
        responses = await self._request([
            [
                "Email/set",
                {
                    "accountId": account_id,
                    "update": {email_id: {"keywords": keywords}},
                },
                "k0",
            ]
        ])

        data = self._parse_response(responses[0], "Email/set")
        not_updated = data.get("notUpdated", {})
        if not_updated and email_id in not_updated:
            err = not_updated[email_id]
            raise FastmailError(
                f"Email/set keywords failed: {err.get('type', 'unknown')} — "
                f"{err.get('description', 'Failed to update keywords')}"
            )

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    async def download_blob(self, blob_id: str) -> bytes:
        """Download a blob (attachment) by ID."""
        if self._session is None:
            raise FastmailError("Not authenticated")

        account_id = self._account_id()
        encoded_blob = quote(blob_id, safe="")
        encoded_name = quote("attachment", safe="")

        url = (
            self._session.download_url
            .replace("{accountId}", account_id)
            .replace("{blobId}", encoded_blob)
            .replace("{name}", encoded_name)
            .replace("{type}", "application/octet-stream")
        )

        if self._client is None:
            raise FastmailError("Client not open")

        resp = await self._client.get(url)
        if resp.status_code == 404:
            raise FastmailError(f"Blob not found: {blob_id}")
        if resp.status_code == 401:
            raise FastmailError("Token expired or invalid")
        if resp.status_code == 429:
            raise FastmailError("Rate limited")
        if resp.status_code >= 500:
            raise FastmailError(f"Server error: {resp.status_code}")

        return resp.content

    # ------------------------------------------------------------------
    # Masked Email
    # ------------------------------------------------------------------

    async def list_masked_emails(self) -> list[MaskedEmail]:
        """List all masked email addresses."""
        self._require_capability(
            "https://www.fastmail.com/dev/maskedemail", "Masked email",
        )
        account_id = self._account_id()
        responses = await self._request([
            ["MaskedEmail/get", {"accountId": account_id, "ids": None}, "me0"]
        ])
        data = self._parse_response(responses[0], "MaskedEmail/get")
        return [MaskedEmail.model_validate(m) for m in data.get("list", [])]

    async def create_masked_email(
        self,
        for_domain: str | None = None,
        description: str | None = None,
        email_prefix: str | None = None,
    ) -> MaskedEmail:
        """Create a new masked email address."""
        self._require_capability(
            "https://www.fastmail.com/dev/maskedemail", "Masked email",
        )
        account_id = self._account_id()

        create_obj: dict[str, Any] = {"state": "enabled"}
        if for_domain:
            create_obj["forDomain"] = for_domain
        if description:
            create_obj["description"] = description
        if email_prefix:
            create_obj["emailPrefix"] = email_prefix

        responses = await self._request([
            [
                "MaskedEmail/set",
                {"accountId": account_id, "create": {"new": create_obj}},
                "me0",
            ]
        ])

        data = self._parse_response(responses[0], "MaskedEmail/set")
        not_created = data.get("notCreated", {})
        if not_created and "new" in not_created:
            err = not_created["new"]
            raise FastmailError(
                f"MaskedEmail/set failed: {err.get('type', 'unknown')} — "
                f"{err.get('description', 'Failed to create masked email')}"
            )

        created = data.get("created", {})
        result = created.get("new")
        if result is None:
            raise FastmailError("MaskedEmail/set: no masked email returned")
        return MaskedEmail.model_validate(result)

    async def update_masked_email(
        self,
        id: str,
        state: str | None = None,
        for_domain: str | None = None,
        description: str | None = None,
    ) -> None:
        """Update a masked email's state, domain, or description."""
        self._require_capability(
            "https://www.fastmail.com/dev/maskedemail", "Masked email",
        )
        account_id = self._account_id()

        update_obj: dict[str, Any] = {}
        if state is not None:
            update_obj["state"] = state
        if for_domain is not None:
            update_obj["forDomain"] = for_domain
        if description is not None:
            update_obj["description"] = description

        responses = await self._request([
            [
                "MaskedEmail/set",
                {"accountId": account_id, "update": {id: update_obj}},
                "me0",
            ]
        ])

        data = self._parse_response(responses[0], "MaskedEmail/set")
        not_updated = data.get("notUpdated", {})
        if not_updated and id in not_updated:
            err = not_updated[id]
            raise FastmailError(
                f"MaskedEmail/set update failed: {err.get('type', 'unknown')} — "
                f"{err.get('description', 'Failed to update masked email')}"
            )
