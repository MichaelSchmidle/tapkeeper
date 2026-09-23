# Container operation

The maintained image uses Python 3.13, the frozen runtime dependency lock and
UID/GID **10001:10001**. It has no Hermes dependency, inbound ports, root runtime,
or embedded configuration/token/history. Initial published builds target **linux/amd64**.

## Build and release

```sh
docker build -t tapkeeper:check .
docker run --rm --network none --read-only tapkeeper:check --help
```

PR/main builds do not publish. After review and merge, the repository owner may
push a version tag such as `v0.1.0`. The image workflow publishes
`ghcr.io/<lowercase-owner>/tapkeeper:v0.1.0` and `sha-<full-commit>`.
No `latest` tag is used. This documentation does not assert a release exists.
Inspect the resulting manifest and record its digest; deployments must use
`ghcr.io/<lowercase-owner>/tapkeeper@sha256:<actual-digest>` rather than a mutable tag.
Verify an authenticated pull (or configure GHCR package public visibility if intended)
before handing the image to a deployment manager. Package permissions and first
registry publication remain release-time checks. Tagging is not deployment permission.

Pinned base images/uv and the runtime lock require deliberate security updates and
rebuilds. A digest preserves identity, not perpetual security. Build context is an
allowlist: do not put private files in the Python package directory.

## Private files and supervision

The [Stacksmith bundle](https://github.com/MichaelSchmidle/stacksmith/tree/main/tapkeeper)
provides Compose configuration and the operating sequence (available after its PR merges).
Keep configuration, token and database outside the checkout on the Docker host.
Use local SQLite-capable storage, not an SMB/NFS database mount. Give the data
directory UID/GID 10001:10001 ownership and mode 0700. The JSON and token files
must be readable by UID 10001, mode 0400 or equivalently restrictive ACLs; mount them
read-only. Docker user namespace/rootless deployments must map these IDs appropriately.

The CLI supports `TAPKEEPER_TOKEN_FILE` pointing to a UTF-8 token file (a final
newline is allowed), with no token contents in environment.
`TAPKEEPER_TOKEN` is rejected. Only `run` loads the token or contacts Telegram; offline
commands need neither. Errors remain redacted. Never pass a token in command arguments.

Run one polling/scheduling container, with restart supervision. Supply mandatory owner/chat IDs, `TZ` and prompt times through environment;
JSON contains only the watch catalogue. Compose defaults are Europe/Zurich and
10:00/20:00; direct CLI callers must export all five values explicitly.
A running/restarting container is **not** proof of healthy Telegram polling or writes.
Use logs, offline `status`, and an authorized visible selection/export to verify
operation. There is deliberately no misleading process-only healthcheck.

## Recovery

Use the CLI `backup` command (SQLite online backup plus integrity check), not a copy
of a running database file. Restore to a new path with `restore`; never overwrite
active storage. Keep Telegram disabled (`--network none`, offline command) throughout
rehearsal. Compare full tables including prompt and replay state as well as CSV.
Back up the matching catalogue JSON, private Docker env-file and token separately and encrypt off-host copies. Schedule
backups using the operator's existing backup system; no backup scheduler is installed
by this image. An optional reviewed [host-side Restic operator](BACKUPS.md) supplies
scheduling, retention and monitored integrity checks. Agree recovery point/time and
backup ownership before cutover; do not enable timers before a restore rehearsal.

Stop the poller before switching database/configuration or rollback. Preserve newer
writes separately; an older snapshot loses changes after its recovery point. A code
downgrade is safe only with a compatible schema. See [operations](OPERATIONS.md) and
[CLI commands](RUNTIME.md). Packaging verification is not production deployment,
a release, an off-host backup setup, or live Telegram acceptance.
