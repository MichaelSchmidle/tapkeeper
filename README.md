# Tapkeeper

**One tap. On record.**

A self-hosted Telegram watch-wear logger: receive a scheduled check-in,
tap a watch, keep a portable record. Deterministic code, not an AI agent.

**Status: first runtime slice implemented and tested with synthetic transports.**
Python 3.12+, pinned python-telegram-bot 22.8 long polling, SQLite and an in-process
scheduler. Live Telegram acceptance, deployment and migration remain unverified.
See the [runtime runbook](docs/RUNTIME.md), [schema](docs/SCHEMA.md), and
[invariant ledger](docs/INVARIANTS.md).

## Scope

- One collector per deployment, with a dedicated Telegram bot and authorized user.
- Morning/evening check-ins, corrections and backfill, and import/export of history.
- Configurable watch catalogue, prompt times, timezone, and delivery destination.
- No Hermes runtime dependency, hosted multi-user service, or collection-management platform.

The software and schema are public; credentials, watch lists and wear history are not.
Telegram still processes bot messages: self-hosted does not mean Telegram-free or
end-to-end encrypted bot conversations.

## Project documents

- [Product and acceptance contract](docs/PRODUCT.md)
- [Architecture and data proposals](docs/ARCHITECTURE.md)
- [Operations and migration plan](docs/OPERATIONS.md)
- [Contributor and agent rules](AGENTS.md)

## License

[MIT](LICENSE). Reuse is welcome; operating a deployment remains its owner's responsibility.
