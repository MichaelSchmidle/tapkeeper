# First-slice runtime runbook

Only `run` contacts Telegram. All verification so far uses synthetic data and fake
Telegram requests; the live acceptance gate is **not passed**. Do not deploy,
import production history, or send messages without separate operator permission.
No Hermes, web server, containers, or external scheduler are required.

## Install and private files

From the checkout with Python 3.12+ and uv:

```sh
uv sync --frozen
uv run --frozen pytest
uv run --frozen ruff check tapkeeper tests
uv build
```

`python-telegram-bot==22.8` was selected from actual PyPI JSON metadata (requires
Python >=3.10); project floor is 3.12. uv.lock locks runtime and test resolution;
the build backend is pinned separately in pyproject.toml.
Use a private directory outside the checkout, restricted to the operator (0700);
CLI sets umask 077. JSON, SQLite, CSVs and backup paths must be outside the checkout.
Example **synthetic** JSON structure, saved privately as `$HOME/.config/tapkeeper/config.json`:

```json
{
  "user": 1,
  "chat": 2,
  "topic": null,
  "timezone": "Europe/Zurich",
  "morning": "08:00",
  "evening": "20:00",
  "watches": {"synthetic.reference": "Synthetic watch"}
}
```

Replace the synthetic destination before authorized live use. IDs are exact, not
normalized; map keys must be unique, nonblank, without control characters, and not
reserved `none` or `same`. Labels are nonblank, up to 100 characters. Removing an ID
retires it in SQLite; historical prompts/import remain valid. Never recycle IDs.
Times are HH:MM and timezone is IANA; host must provide tzdata.

In the following commands, set C and D to private absolute configuration/database
paths, F to a new private CSV, and B/R to new private backup/restore paths.

```sh
uv run --frozen tapkeeper --config "$C" --db "$D" init
uv run --frozen tapkeeper --config "$C" --db "$D" set 2026-03-28 morning synthetic.reference
uv run --frozen tapkeeper --config "$C" --db "$D" export "$F"
uv run --frozen tapkeeper --config "$C" --db "$D" import-legacy "$F"
uv run --frozen tapkeeper --config "$C" --db "$D" import-v1 "$F"
uv run --frozen tapkeeper --config "$C" --db "$D" backup "$B"
uv run --frozen tapkeeper --config "$C" --db "$R" restore "$B"
uv run --frozen tapkeeper --config "$C" --db "$R" status
```

Backup uses SQLite's online backup API plus integrity_check. Restore opens the
source read-only, checks schema/integrity and uses that API to a **new** destination;
existing targets are refused. Keep restored storage isolated with sending disabled.
Back up configuration and credentials separately; encrypt off-host copies. Test a
restored CSV comparison AND replay/prompt state, not only file existence. Opening a
restored database applies its supplied catalogue configuration; retain matching
configuration for faithful recovery. Stop the old process before replacing its DB.
Recovery point equals the selected backup; account for all newer writes explicitly.

Only after live authorization, supply token privately in `TAPKEEPER_TOKEN` (never
in a command argument or checkout) and invoke:

```sh
uv run --frozen tapkeeper --config "$C" --db "$D" run
```

Run exactly one instance under a supervisor. Long polling retains queued updates;
shutdown cancels and awaits the separate scheduler task, rather than registering an
infinite task with Application.create_task. Interval is 15 seconds. Check-ins catch
up only on the current local day. DST gaps advance to the next valid minute; overlaps
use the first occurrence. The machine clock and timezone database must be correct.

Telegram owner commands: `/set YYYY-MM-DD morning|evening WATCH_ID|none|same`,
`/export`, `/help`. `/set` corrects or backfills independently of old bot keyboards.
These are owner AND exact chat/topic restricted. Evening same copies the current
morning snapshot; missing/no-watch morning requires direct selection. A scheduled
prompt always owns its original local date, with no age expiry. A fresh tap corrects
that slot; redelivery of the same callback does not change its timestamp. Telegram
`/set` updates also have durable replay receipts: old redelivery cannot undo a later
correction. A fresh CLI set intentionally creates a new correction.

## Uncertainty and reconciliation

Before every send the transaction stores `uncertain`, increments attempts, and stores
UTC attempt time. A crash before the network call consumes an attempt too. At most
three attempts, at least 60 seconds apart, current day only; never promise exactly
once visible messages. All duplicate visible buttons refer to the same logical slot.
Callbacks remain usable after a send timeout or retry. A successful retry does not
prove that earlier messages were absent. No automatic reset of the cap is provided.

Redacted stderr codes report uncertain send, exhausted attempts, destination mismatch,
callback storage failure, or scheduler storage/clock failure without exception text,
tokens or catalogue/history. Library logging is disabled in `run` because HTTP URLs
can contain bot tokens. `status` reports durable state totals; it is not proof of
working polling. Silent Telegram disconnects and writable storage need an operator
health check in the authorized destination; there is no health web endpoint.

On uncertainty: stop the process, back up, inspect the intended destination privately,
and inspect `SELECT id,state,attempts FROM prompts WHERE state='uncertain'` locally.
If a prompt exists, use its buttons; if absent, backfill using `/set` or offline CLI.
Suppress retries after reconciliation with:

```sh
uv run --frozen tapkeeper --config "$C" --db "$D" reconcile OPAQUE_PROMPT_ID
```

This marks only uncertain prompts reconciled; it never sends, clears history or resets
attempts. For destination mismatch, restore intended configuration or stop and
reconcile/backfill privately; old catalogue is never sent to a new destination.
For storage failures, check permissions, disk and backup integrity before retrying;
never infer success from a running process. Confirmation failure after commit leaves
history durable; retrying the same callback returns its original response safely.

## Release and upgrade

This is schema v1 initialization, not an upgrade of a prior released runtime. A code
rollback is safe only with compatible schema. Before a future schema change stop
writes, backup, test restoration and apply an explicit migration. Owner retains
merge/release authority. Independent review, live authorized scheduled interaction,
restart, operator/backup ownership, and deployment supervision remain release gates.
