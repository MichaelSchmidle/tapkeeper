# Product contract

## Agreed boundaries

Tapkeeper preserves the useful interaction of a personal watch logger without
coupling it to an agent platform: **notification → tap → recorded**.
One user has one dedicated bot. Others may deploy their own instance; this project
does not operate a service for them or support multiple collectors in one instance.

The v1 scope is notify, select, record, correct/backfill, and export. Configuration
covers the watch catalogue, morning/evening prompt times, timezone and Telegram
private 1:1 chat destination. Existing history and stable identifiers must migrate
without loss. The schema is purpose-built and publicly documented, not tied to
one person's private catalogue or filesystem.

Non-goals: LLMs, dashboards, valuations, social features, subscriptions, generic
logging frameworks, and rich collection management. Adding a watch need not
require code changes; a self-service catalogue editor is not a v1 requirement.

Scalar deployment settings come from environment; the JSON file is catalogue-only.
The token stays in a private file. The positive owner ID also determines the private
chat destination; there is no separate chat-ID setting.
One timezone and morning/evening schedule apply; examples use 10:00/20:00 Europe/Zurich.
Group/topic support is out of scope until separately requested.

## Approved v1 behavior

The first runtime slice implements these criteria with synthetic automated evidence;
see [the invariant ledger](INVARIANTS.md). [Partial baseline live evidence](LIVE_ACCEPTANCE.md)
does not close the saved-prompt UX live milestone below.

| Scenario | Expected result |
| --- | --- |
| Morning or evening check-in | Show selectable active watches and “No watch” directly in the message. |
| Authorized owner selects a watch | Commit for the prompt's local date and slot, then edit the tapped message to date/slot and saved selection with one Change button. |
| Owner taps Change | Reopen the offered choices with current saved context; do not change the answer, timestamp or replay history. |
| Owner chooses “No watch” | Save an explicit answer, distinct from no response. |
| Owner does not answer | Do not infer a watch or create a “No watch” record. |
| Evening “Same as morning” | Copy that date's current morning watch into evening; if absent or “No watch”, ask for a direct selection. |
| Morning changes after “Same as morning” | Evening stays a snapshot; it does not silently change. |
| Owner selects a different watch on the same prompt | Replace that slot's answer, not append another current record. |
| Same callback is redelivered | No duplicate record or changed recording timestamp merely from transport replay. |
| A previous day's Tapkeeper prompt is tapped | Save for that original date/slot, not today's date; age alone does not expire it. |
| No usable prompt exists for a past slot | Offer explicit date/slot backfill; exact command syntax is deferred. |
| Future date or malformed selection | Reject without changing history. |
| Someone else or a nonprivate destination submits input | Reject without changing or disclosing private history. |
| Watch is retired from selection | Retain its identity and history; do not recycle its ID. |
| Storage fails | Leave choices unchanged; show a dismissible Telegram error alert and allow retry. |
| Telegram edit/confirmation fails after commit | Keep the saved answer; never call it a failed write. A later tap projects current history. |

A new bot does not inherit the old bot's buttons. Migration must provide a
backfill route independent of those old messages.

## First implementation milestone

Exercise scheduled prompt → authorized tap → durable record → confirmation →
export in Telegram, including restart recovery. Demonstrate correction, old-date
backfill, rejected unauthorized input, and an explicit storage failure against
isolated test data. Unit tests alone do not establish live delivery or recovery.

Stop expanding v1 when this contract and the operations release gate are met.
Further features require a separate scope decision, not speculative scaffolding.
