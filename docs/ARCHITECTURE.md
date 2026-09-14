# Architecture and data

**Status:** product boundaries are agreed in [PRODUCT.md](PRODUCT.md).
Technology and detailed behavior below are proposals for review, not implemented
or benchmarked decisions. Schema is independent of storage and export format.

## Smallest proposed runtime

One supervised Python process, a pinned Telegram bot library
(`python-telegram-bot` is the initial candidate), local SQLite storage and a
local-time scheduler. Use long polling initially: no public inbound web endpoint.
No Hermes imports or reliance on its gateway/scheduler; no LLM, database server
or message broker.
Select supported versions and validate library lifecycle behavior during implementation.

Keep Telegram handling, domain operations and persistence separate enough to test
without the network; do not introduce a plugin framework. Persist prompt identity
and delivery state so callbacks and scheduling do not depend on process memory.

SQLite is proposed for transactions and uniqueness constraints; CSV remains a
portable interchange format. Private configuration and database files live outside
the source tree. Container packaging is a candidate, not yet a supported install path.

## Logical data model

| Entity | Minimum meaning |
| --- | --- |
| Watch | Stable ID, brand, reference, display label, active/retired state. |
| Wear record | Local date, morning/evening slot, watch ID or explicit no-watch answer, recording timestamp and source. |
| Prompt | Durable identity bound to date, slot, destination and delivery state. |
| Processed update | Durable replay identity sufficient to make repeated Telegram delivery harmless. |

One current wear record per `(local_date, slot)`. No row means unanswered;
an explicit no-watch answer is not missing data. Timestamps include an offset
or use UTC; the wear date is the prompt's calendar date in the configured IANA
timezone. Changing timezone must not reinterpret historical dates.

Preserve imported watch IDs exactly. The legacy convention is `brand.reference`,
with exact reference spelling; labels are presentation, never keys. Retire watches
rather than deleting referenced identities. A retired watch may still be accepted
from a valid historical prompt. Never trust a callback-supplied ID without lookup.

Version the persisted schema and interchange contract explicitly. Define the
actual tables, fields and migrations with implementation, not through undocumented
storage side effects. No tenant or multi-user machinery is needed.

## CSV compatibility

The legacy wear-log columns are `date,slot,watch_id,watch,recorded_at,source`.
An existing row with empty watch ID and label represents explicit no-watch;
absence of a row represents unanswered. Preserve IDs, timestamps, source and
historical labels on migration, including labels that differ from today's catalogue.

Import must validate the entire input before committing, report unknown IDs and
conflicting duplicate date/slot rows, and never silently discard or overwrite them.
Re-import of identical data must not duplicate history. Define encoding, field
rules and version signaling in the implementation's interchange specification;
retain an explicit legacy import path. Export is a snapshot, not a second writable
source of truth. No live bidirectional CSV synchronization in v1.

## Reliability and security invariants

| Invariant | Required evidence |
| --- | --- |
| Only the owner can mutate records | Wrong user, chat or topic rejected before write; valid owner succeeds. |
| Prompt owns date and slot | Forged/malformed callback cannot redirect a write; historical prompt survives restart. |
| Selection is atomic and replay-safe | Concurrent/replayed updates leave one consistent answer; transaction failure leaves no partial state. |
| Confirmation follows commit | Inject storage failure; no success acknowledgement. Commit followed by Telegram failure retains the record and permits safe retry. |
| Local schedule survives DST/restart | Clock-driven tests cover DST transitions, restarts and missed prompts. |
| History is portable | Synthetic legacy import/export round-trip preserves every record and identifier. |
| Recovery is real | Restore a backup into isolated storage and reproduce expected records. |

Authorize both the configured Telegram user and destination. A group membership
check alone is not authorization. Treat callback data as untrusted and bind it
to a known prompt. Secrets must never appear in logs or error messages.

Proposed scheduling policy: one logical prompt per date/slot; on restart send
missed prompts for the current local day only, with their intended date/slot.
Older days remain available through backfill, without a catch-up message flood.
For a DST gap, use the next valid local time; for an overlap, use the first occurrence.
Test these policies before claiming support.

Telegram send and local commit cannot be one transaction. A send timeout can
leave delivery uncertain; do not promise exactly-once visible messages. Record
uncertainty, bound retries, and ensure duplicate visible prompts cannot corrupt
or duplicate wear records. Final retry/reconciliation mechanics need a focused
implementation decision and failure tests.
