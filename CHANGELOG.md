# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2025-07-20

### Added

- **JMAP client** (`JmapClient`) — full email client supporting list, search,
  send, reply, forward, move, mark read/spam, download attachments, thread
  fetching, and masked email management
- **CalDAV client** (`CalDavClient`) — calendar discovery and CRUD, event CRUD
  with ETag-based optimistic concurrency, UID REPORT fallback, concurrent
  per-calendar fetches with partial-failure tolerance
- **CardDAV client** (`CardDavClient`) — address book discovery, contact and
  contact group CRUD, group member management, concurrent per-book fetches
- **iCalendar module** — hand-rolled RFC 5545 parser and serializer with
  `icalendar` library fallback, date range helpers (`default_today_range`,
  `current_week_range`, `parse_range_start`/`parse_range_end`)
- **Pydantic v2 models** for all domain objects — `Email`, `Mailbox`, `Identity`,
  `MaskedEmail`, `Calendar`, `CalendarEvent`, `EventQuery`, `Contact`,
  `ContactGroup`, `AddressBook`, and more
- **Typed exception hierarchy** — `FastmailError`, `NotAuthenticated`,
  `CalendarNotFound`, `EventNotFound`, `CalendarConflict`, `EventConflict`,
  `CalDAVServerError`
- **Credential loading** from environment variables or Rust `fastmail-cli`
  config file (`~/.config/fastmail-cli/config.toml`)
- Full async/await support using `httpx` as the HTTP transport

[0.1.0]: https://github.com/kwhatcher/fastmail-py-sdk/releases/tag/v0.1.0