# Product contract

## Agreed boundaries

Tapkeeper preserves the useful interaction of a personal watch logger without
coupling it to an agent platform: **notification → tap → recorded**.
One user has one dedicated bot. Others may deploy their own instance; this project
does not operate a service for them or support multiple collectors in one instance.

The v1 scope is notify, select, record, correct/backfill, and export. Configuration
covers the watch catalogue, morning/evening prompt times, timezone and Telegram
chat/topic destination. Existing history and stable identifiers must migrate
without loss. The schema is purpose-built and publicly documented, not tied to
one person's private catalogue or filesystem.

Non-goals: LLMs, dashboards, valuations, social features, subscriptions, generic
logging frameworks, and rich collection management. Adding a watch need not
require code changes; a self-service catalogue editor is not a v1 requirement.

## Approved v1 behavior

The first runtime slice implements these criteria with synthetic automated evidence;
see [the invariant ledger](INVARIANTS.md). The live milestone below is still open.

| Scenario | Expected result |
| --- | --- |
| Morning or evening check-in | Show selectable active watches and “No watch” directly in the message. |
| Authorized owner selects a watch | Save for the prompt's local date and slot; confirm the saved selection. |
| Owner chooses “No watch” | Save an explicit answer, distinct from no response. |
| Owner does not answer | Do not infer a watch or create a “No watch” record. |
| Evening “Same as morning” | Copy that date's current morning watch into evening; if absent or “No watch”, ask for a direct selection. |
| Morning changes after “Same as morning” | Evening stays a snapshot; it does not silently change. |
| Owner selects a different watch on the same prompt | Replace that slot's answer, not append another current record. |
| Same callback is redelivered | No duplicate record or changed recording timestamp merely from transport replay. |
| A previous day's Tapkeeper prompt is tapped | Save for that original date/slot, not today's date; age alone does not expire it. |
| No usable prompt exists for a past slot | Offer explicit date/slot backfill; exact command syntax is deferred. |
| Future date or malformed selection | Reject without changing history. |
| Someone else taps in a shared topic | Reject without changing or disclosing private history. |
| Watch is retired from selection | Retain its identity and history; do not recycle its ID. |
| Storage fails | Do not claim “recorded”; report failure and allow retry. |

A new bot does not inherit the old bot's buttons. Migration must provide a
backfill route independent of those old messages.

## First implementation milestone

Exercise scheduled prompt → authorized tap → durable record → confirmation →
export in Telegram, including restart recovery. Demonstrate correction, old-date
backfill, rejected unauthorized input, and an explicit storage failure against
isolated test data. Unit tests alone do not establish live delivery or recovery.

Stop expanding v1 when this contract and the operations release gate are met.
Further features require a separate scope decision, not speculative scaffolding.
