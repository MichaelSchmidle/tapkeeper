# Schema and interchange v1

SQLite `PRAGMA user_version=1`. New empty databases are initialized transactionally;
unknown versions and nonempty unversioned databases are rejected. There are no
legacy database migrations. Upgrade from this first schema requires an explicit
future migration, backup and rollback plan; never edit user_version to bypass checks.

## Physical tables

- `watches(id TEXT PRIMARY KEY, label TEXT, active INTEGER)`: exact stable IDs.
  Runtime JSON maps ID to display label. Brand/reference are not parsed from IDs;
  retain richer catalogue metadata separately if needed. Opening configuration
  marks absent watches retired without deleting them. Never recycle an ID.
- `records(date,slot,watch_id,watch,recorded_at,source)`: all TEXT, NOT NULL;
  primary key `(date,slot)`, morning/evening slot check. Empty ID AND label means
  explicit no-watch. No row means unanswered. Label is a recording-time snapshot.
- `prompts(id,date,slot,user,chat,topic,choices,state,attempts,last_attempt,message_id)`:
  opaque 96-bit random ID, unique `(date,slot)`, immutable destination and JSON
  offered catalogue snapshot. States pending/uncertain/sent/reconciled; attempts
  at most 3. `last_attempt` is UTC offset-aware ISO text, message ID nullable.
- `callbacks(id PRIMARY KEY,response)`: namespaced `callback:QUERY_ID` or
  `update:UPDATE_ID` replay receipts and original confirmation. The latter covers
  Telegram `/set`; fresh offline CLI sets intentionally remain corrections.
  Inserted atomically with the answer. Replay returns that response,
  never rewrites history, even following another correction. A reused callback ID
  with another valid payload retains the original result, not the second mutation.

Domain validation supplements SQL constraints; direct SQL writes are unsupported.
One active polling process per database is an operator prerequisite. Selection and
backfill use BEGIN IMMEDIATE, rollback on failure, and SQLite durable commit before
confirmation. Prompts and replay rows have no automatic retention expiry.

Callback payload is `opaque_prompt_id:choice_index` (or `:none`, `:same`), never a
watch ID. Persisted offered order resolves indices; Unicode/long IDs remain exact.
Known, single-attempt successful sends bind the message ID too. An uncertain send
may have been visible without a returned ID. After retry, the first message must
remain usable: durable token + configured and stored user/chat/topic authorization
are authoritative when attempts exceed one. Duplicate messages cannot produce
multiple current rows. Retired historical offered choices still resolve.
When Telegram omits topic metadata, recover it only from an exact persisted
prompt-token/chat/message-ID match on a non-pending prompt. Current owner and
configuration plus stored destination checks still apply. An unknown message
(including an unbound uncertain-send duplicate) cannot use this fallback; use
backfill if its topic metadata is also unavailable.

## CSV v1 and explicit legacy path

UTF-8 without BOM, header exactly:

```text
date,slot,watch_id,watch,recorded_at,source
```

v1 intentionally has identical columns to legacy. Version is selected explicitly
by `import-v1` versus `import-legacy`; filename `tapkeeper-v1.csv` signals export
version. There is no magic comment/header preamble. Export uses LF, standard CSV
quoting and double-quote escaping. Import accepts standard CSV line endings.

Date must be exact YYYY-MM-DD, real and not future in configured timezone. Slot is
morning/evening. Watch IDs must already exist (active or retired) in this database;
load all legacy IDs into private configuration before import, then retire them.
Nonempty ID requires nonblank historical label; no-watch requires both empty.
Recorded_at must parse as an offset-aware ISO datetime, not future. Source must be
nonblank. Labels, IDs, timestamp spelling/offset and source are preserved exactly,
not normalized. Extra/missing fields, unknown IDs, malformed timestamps and
conflicting duplicate or existing date/slot rows reject the entire import.
Identical duplicates/reimport are no-ops. Validation precedes writes; late conflicts
also roll back all inserted rows. Export is ordered by date, morning then evening.

CSV contains private history. Spreadsheet formulas (including leading `=`, `+`,
`-`, `@`) are NOT silently rewritten: that would break lossless migration. Import
as text in a trusted spreadsheet or use a plain-text CSV tool; never blindly execute
spreadsheet content. CSV is not a full backup: prompt/replay state is absent.
