# Scheduled encrypted backups

The optional Linux host operator in `ops/backup.py` uses the published image's
native `backup` command, then Restic. It does not add a daemon or change the image.
Dependencies: Python 3.12+, root access to Docker, Restic (tested with 0.16.4),
`findmnt`, and systemd for the supplied schedules. The database must be named
`tapkeeper.db` inside `data_dir`, on local storage. The image must already be
pulled by digest. Only the offline backup command runs, with container networking
disabled; the Telegram token is copied into the encrypted recovery payload but
never passed to the backup container.

## Scalar settings

Set operator `env_file` to the same private Docker env-file used for Compose
(`docker compose --env-file /private/settings.env ...`). It must explicitly contain
`TAPKEEPER_USER_ID`, `TZ`, `TAPKEEPER_MORNING` and
`TAPKEEPER_EVENING`; defaults/examples are 10:00/20:00 Europe/Zurich. Do not maintain
a second scalar JSON source. For Portainer, retain a matching private env-file and
update it together with stack settings under the backup lock before restarting.
Do not override these settings through shell environment during deployment.

Use mode 0600 or 0400 and literal Docker `KEY=value` lines: no quotes, expansion,
inline comments, duplicate keys or implicit host inheritance. Blank lines and full-line
comments are allowed. Optional deployment keys are `TAPKEEPER_IMAGE`,
`TAPKEEPER_CONFIG_FILE`, `TAPKEEPER_TOKEN_FILE` (path only), `TAPKEEPER_DATA_DIR`.
Obsolete `TAPKEEPER_CHAT_ID` entries are tolerated for existing env-files but ignored
by the runtime; the owner ID determines the private destination. Token contents and
all other keys are rejected. Docker loads the file; the operator
only validates this restricted format, freezes its bytes for `--env-file`, and archives
it as `settings.env`. It passes no token mount to the offline container. An optional
token-file path in environment is inaccessible and is never read by offline commands.
Changes to catalogue, env-file or token during the native snapshot abort before Restic.
Existing operational installations are unchanged; installation/requalification needs
separate approval, including adoption of catalogue-only JSON and the matching image.

## Safety and recovery contract

- One private Restic repository per application. A dedicated NAS account should
  have access only to its backup subtree; do not replace another application's
  existing mount credentials. Verify access denials through the actual protocol.
- Store the Restic encryption password independently in a password manager **before**
  initialization, and keep a root-only operational password file. The NAS login
  password is separate and can be reset by the NAS administrator after host loss.
- The mounted source, mountpoint and filesystem type are checked before each Restic
  operation; an autofs entry or local fallback directory does not qualify. Network
  mount loss can still cause I/O errors; no success heartbeat follows a failure.
- One nonblocking lock covers snapshot, retention and checks. A missing database
  fails rather than silently creating an empty source. The native CLI opens the
  source using the normal Store initialization, so its data mount is writable;
  keep its config/image identical to the poller's, and serialize upgrades/config
  edits with the backup lock. Catalogue/environment/token changes during the snapshot
  abort the run. Do not change image/schema while a backup is running.
- Unique restricted local staging is removed on normal success, error, SIGTERM
  or SIGINT, before success notification. SIGKILL, power loss or filesystem errors
  can leave staging or a disposable container: inspect and clean those deliberately;
  do not blindly delete paths or restart a timer after interrupted recovery work.
- Payload: native SQLite snapshot (including prompt and replay state), matching
  catalogue JSON, scalar environment file and token, image metadata, operator config/script and optional
  recovery files. Restic/NAS passwords are deliberately not embedded. Protect
  restored payloads: application secrets and monitor push URLs are inside them.
- Keep 7 daily, 4 weekly and 6 monthly buckets for `tapkeeper-automated`, grouped by
  host/tags, **not transient staging paths**. Other tags are excluded from forgetting.
  Weekly `check` prunes unreferenced data and checks repository integrity; monthly
  `fullcheck` reads all data. All operations share the application lock.
- Use an off-host deadline monitor (supported: Uptime Kuma push endpoints returning
  `{"ok":true}`): one heartbeat for successful backups, a different one for checks.
  Checks never renew backup freshness. `--scheduled` refuses missing monitor URLs.
  Weekly/monthly checks share a deadline: monthly omission alone is not independently
  detected if weekly checks succeed. Monitor deadlines, not only explicit failures.
- Nightly backups accept up to a day's lost entries. A writable encrypted repository
  is neither immutable nor off-site protection. Root/Docker administrators on the
  application host remain trusted and can access credentials or delete backups.

## Install and qualify

After review, install from a trusted checkout. Adjust the example's paths and image;
never put real config, credentials or heartbeat URLs in Git. Defaults schedule backup
at 03:30 daily, checks Sunday 13:00 and full checks on the first of the month at 07:00,
all Europe/Zurich. Adjust for your other workloads and recovery objectives.

```sh
sudo install -d -m 0700 /etc/tapkeeper-backup /srv/tapkeeper/backup-stage
sudo install -m 0755 ops/backup.py /usr/local/sbin/tapkeeper-backup
sudo install -m 0600 ops/config.example.json /etc/tapkeeper-backup/config.json
sudo install -m 0644 ops/tapkeeper-backup@.service ops/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

Create the private config, application files and independent encryption-key copy.
The application JSON and local data directory must be accessible to image UID/GID
10001:10001; stage parent and backup credentials stay root-only. Configure a persistent
NAS mount and verify unmounted-directory protection and application-specific access.
Initialize Restic once, never automatically as part of a backup. Verify that an
independent re-fetch of the vaulted key unlocks the repository.

Keep schedules disabled until all gates pass:

1. Record your secret-free recovery procedure both locally and **on the NAS outside
   encryption**. Give only the intended application account/admin access to its copy.
   Include fresh-host bootstrap, exact key location, repository path, image digest,
   configuration, snapshot selection, restoration and rollback/cutover boundaries.
2. With synthetic isolated application state and no Telegram networking, run the
   candidate manually (heartbeat fields may be blank only for this rehearsal):
   `sudo /usr/local/sbin/tapkeeper-backup --config /private/test.json backup`.
3. List the encrypted snapshot, restore with Restic `--verify`, then use the image's
   native `restore` to a fresh database. Compare **every table**, exported CSV,
   catalogue, environment and token; run `fullcheck`. Remove temporary plaintext and containers.
   Retag a retained rehearsal as `tapkeeper-rehearsal`, removing `tapkeeper-automated`.
4. Configure separate authenticated HTTPS push monitors, for example backup deadline
   26 hours and checks 9 days, using the existing notification route. Test explicit
   failure, a shortened missed deadline and recovery, then restore the real deadline.
   Do not confuse accepted notifications with confirmed handset delivery.
5. With the final private configuration and authorized source database in place,
   execute the **actual service units**, not just the script. Inspect exit status and
   monitor readback. Preview retention and confirm unrelated/manual tags survive.
6. Only then enable timers and inspect next occurrences:

```sh
sudo /usr/local/sbin/tapkeeper-backup --config /etc/tapkeeper-backup/config.json retention-dry-run
sudo systemctl start tapkeeper-backup@backup.service
sudo systemctl start tapkeeper-backup@check.service
sudo systemctl start tapkeeper-backup@fullcheck.service
sudo systemctl enable --now tapkeeper-backup.timer tapkeeper-check.timer tapkeeper-fullcheck.timer
systemctl list-timers 'tapkeeper-*'
```

These instructions do not authorize production import, poller startup or replacement
of live state. An isolated rehearsal is not a backup of real history.

## Restore without the original host

1. On a trusted replacement host install Docker and Restic; mount/copy the NAS
   repository read-only using NAS administrator access if necessary. Retrieve the
   encryption key independently. Never expose it in command arguments or chat.
2. List snapshots with `restic --repo /path/to/repository --no-lock snapshots` and
   restore a deliberately selected ID with `--no-lock restore ID --target /private/restore
   --verify`. Omit `--password-file` to use the secure prompt. Do not run maintenance
   while reading a read-only repository. The snapshot's saved `paths` identify the
   payload beneath the restore destination, not a hard-coded staging name.
3. Inspect `image.json` and private configuration; pull the exact image digest.
   Use a fresh UID/GID-10001-owned directory, copy in the snapshot, and run the native
   [restore command](CONTAINERS.md#recovery) with `--network none`, a new DB path and
   matching catalogue JSON and `--env-file` pointing to restored `settings.env`. Never run `run` during the drill.
4. Verify tables, CSV, config and credentials; verify filesystem permissions. Adapt
   saved host paths and recreate/reset NAS access independently. Rehearse scheduling
   and monitoring before enabling them. Preserve newer writes before an authorized
   rollback; restoring old state intentionally loses updates after its recovery point.

## Ownership and retirement

The deployment operator owns scheduling, key custody, monitoring and periodic restore
rehearsals while Tapkeeper is in use. Disable timers before removing units/script or
moving the service. Keep the repository and independent recovery key until a deliberate
retention decision; uninstalling automation does not authorize deleting backups.
