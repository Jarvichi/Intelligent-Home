# Changelog

## [0.1.1] - 2026-11-27

### Fixed
- "Too many open files": every request runs in its own thread and each thread kept its database connection open forever. The connection is now closed at the end of each request (`DB.release()` in ihlib), so the add-on no longer stops answering and gets restarted by the watchdog.

## [0.1.0] - 2026-11-18

### Added
- First version. Token-protected API for the AI Layer: `GET /messages` (search), `GET /messages/<id>` (one message as plain text), `POST /reply`, `POST /send`.
- Reading never marks mail as read. Attachments are never returned, only their names. HTML is turned into plain text. The DMARC result is shown for each message.
- Sending needs `confirmed: true`, at most `max_recipients` (3) and at most `max_per_day` (5) a day. The counter is in the add-on's own `/data/ih.db`. Replies keep the thread (In-Reply-To, References, "Re:") and go only to the sender (or Reply-To).
- The log holds only the time, the kind and a recipient count: never a subject, an address or a body.
- Status page (ingress): sent today, last read, last send, last error.
