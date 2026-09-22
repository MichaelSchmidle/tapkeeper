# Live acceptance evidence

## First runtime baseline

The merged runtime at `abb973ad45c89a69eae71fff0d0da7aa4b2dfc16` was exercised
against Telegram with a dedicated bot, one authorized private chat, an isolated
SQLite database and a synthetic two-watch catalogue. No production history,
migration or existing logger was involved. These results describe that baseline,
not live verification of subsequent UI changes.

Verified with operator observation plus stored state:

- Current-day schedule catch-up delivered both check-ins.
- Morning selection and evening “Same as morning” saved correctly.
- After a process restart, the original morning message remained usable for a
  correction; evening retained its original snapshot and timestamp.
- Explicit prior-date “No watch” backfill produced the expected confirmation.
- Telegram delivered a CSV whose fields matched the database, including historical
  dates, labels, recording timestamps, source and explicit empty no-watch fields.
- Native backup and isolated offline restore preserved every field in catalogue,
  records, prompts and replay receipts; restored integrity check passed.
- A disposable harness set the runtime SQLite connection to `query_only=ON`.
  Live callbacks failed with storage diagnostics, without changing records or
  replay receipts. The operator saw immediate feedback but could not read its
  exact wording before the toast disappeared.
- After stopping that harness and restarting the unmodified writable runtime,
  a fresh tap successfully saved and confirmed the selection.

A first fault-test attempt was inconclusive because its bounded test process had
already stopped. The fault test was repeated while the process was running;
queued callbacks alone were not treated as proof of visible feedback.

## Usability findings

The operator reported that unchanged choice buttons made answered prompts look
pending. Error toasts also disappeared too quickly to read. These findings motivate
the saved-state/Change-button interaction and dismissible failure alerts.

## Remaining gates

- The changed saved-state UI and dismissible alerts need live operator acceptance
  against their exact reviewed commit; baseline testing does not cover them.
- Future scheduled-time delivery (rather than restart catch-up) remains unverified.
- Unauthorized interaction and group/topic behavior have synthetic coverage but
  were not exercised live in this private-chat test.
- Supervision, polling health, production backup ownership, installation/upgrade
  and rollback, cutover and release authorization remain operations gates.

This is bounded acceptance evidence, not a production-readiness claim.

Configuration update note: the group/topic coverage mentioned above describes that
historical baseline. Current configuration permits private 1:1 chats only; no new
live acceptance, deployment or migration is claimed by this change.
