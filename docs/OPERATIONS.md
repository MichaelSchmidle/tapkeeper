# Operations and migration

**Status: partial live baseline acceptance completed; release gate remains open.**
[Runtime commands and reconciliation](RUNTIME.md) and [schema/interchange](SCHEMA.md)
are implemented. [Live acceptance evidence](LIVE_ACCEPTANCE.md) records the isolated
private-chat exercise and distinguishes it from the new UI's pending live checks.
No production deployment or migration has been performed. The release requirements
below still apply.

## Ownership and maintenance

Each deployer owns their bot account/token, host, private configuration, data and
backups. The repository owner controls product scope and merges; contributors
provide reviewed changes. Public source does not promise a hosted service or SLA.
Before production, name the deployment operator and backup owner, choose the
host/packaging, and agree an acceptable recovery point and recovery time.

Keep the service small enough to maintain while it serves the logging habit.
Dependency/security updates, backup checks and occasional Telegram API changes
remain work even without Hermes. If maintenance outweighs its usefulness, stop
scheduling, export the history, and retire the service without data lock-in.

## Container packaging

See [the container runbook](CONTAINERS.md) for the maintained image and Stacksmith
Compose path. The optional [host backup operator](BACKUPS.md) provides Restic
scheduling and integrity checks. Neither packaging nor installation closes cutover gates.

## Deployment requirements

- Dedicated bot token, one authorized owner ID, configured chat and optional topic.
- Explicit timezone and morning/evening times; validated catalogue with stable IDs.
- Private secrets/configuration and durable storage outside the checkout/image.
- One active polling/scheduling instance; supervision restarts it after failure.
- Bounded retries and diagnostics for Telegram disconnection, uncertain delivery,
  invalid configuration, and failed writes. Never log tokens or full wear history.
- Health evidence distinguishes a running process from working polling, writable
  storage and successful scheduling. Document what requires operator intervention.

Public examples and tests use synthetic data only. Bot tokens, personal Telegram
IDs, collections, history, hostnames and deployment paths do not belong in Git,
issues, PR descriptions or CI artifacts. Telegram receives message contents;
bot chats are not an end-to-end encrypted storage channel.

## Backup, upgrade and rollback

Back up the database using a SQLite-consistent mechanism, plus configuration and
securely handled credentials. Do not assume copying an active database file is a
complete backup. Encrypt off-host copies and test restoration in an isolated
location with Telegram sending disabled. CSV export alone does not restore prompt
or replay state.

Before upgrade: stop writes as required, capture a consistent backup, record the
running release and schema version, then apply the documented migration. Verify
history and the full interaction path. Code downgrade alone is not rollback after
an incompatible schema migration: restore a compatible snapshot and explicitly
account for any writes made since that snapshot. Never silently discard them.

## Migration from an existing logger

1. Preserve a read-only snapshot of the old catalogue and history. Inventory its
   schema and any unresolved/duplicate entries without changing the originals.
2. Import into isolated Tapkeeper storage. Compare every record, ID, timestamp,
   source and historical label, not just row totals. Resolve conflicts explicitly.
3. Exercise the new bot with synthetic data in an authorized test destination.
   Verify scheduled delivery, correction, backfill, authorization and restart.
4. Agree the cutover time. Pause the old prompts if not already paused, and disable
   old write paths before taking the final snapshot/import; avoid dual writers.
5. Enable the dedicated bot and verify an owner-authorized real selection and
   export. Old-bot keyboards do not transfer; document the new backfill path.
6. Retain the old snapshot and a rollback plan until acceptance. Remove obsolete
   integrations only with operator approval; never delete historical data as cleanup.

## First-release gate

- Product scenarios and architecture invariants have passing automated evidence.
- A live Telegram workflow and restart have been exercised with explicit permission.
- Installation, export, backup/restore, upgrade and rollback instructions are tested.
- Cutover and recovery ownership are assigned; private deployment details remain private.
- Independent review is complete; the repository owner authorizes merge/release.

A test run against temporary storage is not evidence that production was migrated.
