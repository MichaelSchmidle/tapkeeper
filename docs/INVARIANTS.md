# First-slice invariant ledger

Saved-prompt evidence is synthetic and local to this worktree. See
[partial baseline live evidence](LIVE_ACCEPTANCE.md) for earlier runtime observations;
new UI live acceptance, migration, deployment and release approval are not implied.

| Invariant | Implementation boundary | Regression evidence |
| --- | --- | --- |
| Owner AND exact private chat AND stored prompt destination | Store.select, Application command handler | test_authorization_invalid_atomic; test_wrong_identity_neither_writes_nor_exports (including missing user); test_destination_change_does_not_send_old_catalogue |
| Original date/slot, indefinite old buttons, retired exact IDs | Durable prompts/choices; index callbacks | test_full_restart_correction_snapshot_export; test_retired_prompt_long_exact_id |
| Unknown/malformed/future input makes no record | Config and Store validation | test_malformed_and_absent_callback; test_authorization_invalid_atomic; test_config_invalid_inputs; test_reject_duplicate_config_keys |
| One current answer; correction and explicit no-watch | records primary key; upsert | test_full_restart_correction_snapshot_export; Application command tests |
| Same-as-morning is a copy, not a live link; missing/no-watch cannot be inferred | Store._save | test_full_restart_correction_snapshot_export; test_authorization_invalid_atomic; test_missing_morning_guides_owner_to_direct_selection |
| Telegram /set replay cannot undo a later correction | Namespaced update receipt in the backfill transaction | test_command_replay_after_later_correction |
| Callback ID and record commit atomically | BEGIN IMMEDIATE; callbacks insert | test_callback_insert_failure_rolls_back_record; test_commit_fail_and_confirmation_fail; transaction regression |
| Replay never changes record timestamp, including after correction/restart | Durable callbacks response | test_full_restart_correction_snapshot_export; test_backup_restores_prompts_and_callback_identity |
| Success only after commit; post-commit send failure retains record | Adapter.callback | test_commit_fail_and_confirmation_fail; Application storage-failure command test |
| Current-day catchup only; gap next valid minute/overlap first occurrence | due_time; Adapter.tick | test_dst; test_claim_crash_and_current_day_only |
| Claim uncertainty commits before network, survives process death; <=3 attempts >=60s apart | Store.claim; Adapter.tick | test_retry_uncertainty_restart_bound; test_abrupt_process_exit_after_claim_preserves_uncertainty |
| Duplicate visible uncertain prompts stay usable after successful retry | Opaque prompt identity; message binding only for single certain send | test_first_uncertain_visible_prompt_remains_usable_after_retry; test_actual_application_update_dispatch rejects wrong single-send message |
| Real library handler/lifecycle tested without network | Fake BaseRequest, actual Update and Application | test_actual_application_update_dispatch; tests/test_commands.py; test_scheduler_stop_cancels_inflight_send |
| Whole import validates; unknown IDs/conflicts reject atomically; historical text retained | import_csv | test_backfill_roundtrip_legacy_backup; test_import_late_conflict_rolls_back_all; test_csv_validation_table_and_unicode_losslessness |
| SQLite-consistent backup/restore includes prompt and replay state | backup API, integrity/schema check, new destination only | test_backup_restores_prompts_and_callback_identity; test_cli_process_restart_and_backup |
| CLI is runnable across process boundaries, export/import exact | installed package entrypoint; CLI subprocesses | test_cli_process_restart_and_backup; isolated installed-wheel import smoke |
| Redacted actionable uncertainty diagnostics | Adapter diagnostic codes; run disables library logging | test_uncertain_send_diagnostics_are_redacted |
| CLI imports preserve embedded CRLF/CR and export/reimport stays exact | newline-preserving file reads | test_cli_csv_preserves_embedded_line_endings |
| Historical topic bindings reject even with an exact persisted message binding | Store.select and PTB handler | test_historical_topic_rejected_with_exact_persisted_binding |
| Telegram backfill preserves whitespace-bearing IDs | Raw command tail and optional JSON string | test_backfill_preserves_exact_whitespace_ids |

## Saved-prompt UX regressions

| Invariant | Regression evidence (`tests/test_saved_prompt.py`) |
| --- | --- |
| Change is read-only, keeps saved context and timestamps; restart/replay renders current history while receipts remain original | test_saved_change_replay_restart_projection |
| Change requires exact owner/destination and rejection of historical topic bindings | test_change_authorization_and_topic_binding |
| Failed writes leave choices and show an alert, never saved feedback | test_failed_write_keeps_choices_and_alerts; test_dispatch_change_alert_and_set_reconciliation |
| A second connection sees the committed record before edit; failed edit does not become a failed save | test_edit_failure_is_not_write_failure_and_replay_repairs |
| Real PTB edit/alert dispatch; not-modified is benign; network failures retain history | test_dispatch_change_alert_and_set_reconciliation; test_transport_edit_noop_and_network_failure |
| Same-as-morning projection remains a snapshot; sending after offline backfill uses saved state | test_same_snapshot_and_send_after_offline_backfill |
| /set replay projects current history; post-commit projection read failure does not report a failed write | test_set_replay_and_postcommit_projection_failure |

RED evidence: new UX tests first produced **6 failures** before implementation. A later
focused post-commit `/set` read-failure regression also failed before its boundary fix.
GREEN: `uv run --frozen pytest` and `uv run --frozen --python 3.12 pytest`
each passed **40 tests**, on Python **3.13.13** and **3.12.3** respectively.
`uv run --frozen ruff check tapkeeper tests` and `git diff --check` passed.
The older build/wheel execution record below belongs to the baseline, not this UX slice.
New UI live acceptance remains pending; see [baseline evidence](LIVE_ACCEPTANCE.md).

## Error feedback and authorization evidence

`tests/test_safety.py` exercises the real PTB dispatcher with fake HTTP and seeded,
isolated SQLite data. This is automated boundary evidence, not live Telegram
acceptance or permission to deploy.

| Invariant | Regression |
| --- | --- |
| Storage failure has short retry/storage advice; unavailable “Same as morning” has quoted, direct-selection advice; invalid/unauthorized callbacks disclose no history or raw exception | test_error_copy_distinguishes_storage_domain_and_invalid_selection |
| A storage failure after resolving “Same as morning” is still a storage error, with no record or receipt committed | test_storage_failure_after_same_resolution_is_not_domain_advice |
| After store/dispatcher restart, wrong user/chat/topic cannot mutate seeded history, replay a receipt, reopen saved choices, or export; missing command user and nonprivate chats are rejected; authorized controls still work | test_seeded_history_and_replay_are_private_after_restart |
| Historical owner/chat/topic bindings and newly configured private identities reject old prompts; records, prompts and receipts remain unchanged | test_reconfigured_identity_cannot_reopen_or_replay_old_prompt |

The copy assertions failed against the unchanged runtime at `f8fdd2a` before the
fix. These failures reproduce misleading feedback, **not an authorization bypass**.
At that historical checkpoint, authorization rules and schema were unchanged. Callbacks retain dismissible alerts;
commands from unauthorized destinations remain silent. That checkpoint retained a missing-topic fallback; the current private-only contract
removes it and rejects historical topic bindings.

## Execution record

RED before implementation: `python3 -m unittest discover -s tests -v` failed with
`ModuleNotFoundError: No module named 'tapkeeper'`. Subsequent edge RED runs showed
wrong-destination sending, None callback exception, wrong-message acceptance, and
first uncertain visible prompt rejection; fixes were followed by passing tests.
Tests use unittest lifecycle fixtures under pytest; pytest is the canonical runner.

Historical baseline commands (not validation of the current configuration change):

```sh
uv sync --frozen
uv run --frozen pytest
uv run --frozen --python 3.12 pytest
uv run --frozen ruff check tapkeeper tests
uv build
uv run --isolated --no-project --with ./dist/tapkeeper-0.1.0-py3-none-any.whl python -I -c 'import tapkeeper, telegram; from tapkeeper.cli import main; print("installed wheel imports OK; PTB", telegram.__version__)'
git diff --check
```

Review regressions first reproduced all three requested changes (3 failed, 29 passed).
Full suite after corrections: **32 passed** on Python **3.13.13** and **3.12.3**. Ruff clean; source
distribution and wheel built; isolated installed-wheel import printed
`installed wheel imports OK; PTB 22.8`. No live Telegram acceptance was run.
Rerun the full suite after corrections and update the evidence when results change.

## Open release gates / limits

- Explicitly authorized live scheduling → tap → confirmation → export and restart.
- Dedicated bot setup, polling health under real network failure, supervisor/service
  setup, private file permissions and clock/tzdata on the deployment host.
- Independent review and owner merge/release; operator/backup owner and recovery targets.
- Real legacy catalogue/history validation and cutover authorization. No migration run.
- Exactly-once visible delivery is impossible across Telegram and SQLite; the bounded
  uncertain-send policy is intentional. One active process is an operational rule.
- SQLite schema v1 is the first initialization; no upgrade migration from an earlier
  Tapkeeper release exists. Catalogue runtime fields are stable ID and label; richer
  metadata remains outside the runtime, never inferred/normalized from IDs.

## Environment configuration regressions

`test_environment.py` covers mandatory scalars, stale/mixed JSON rejection before
Store/token loading, matching positive private IDs, exact catalogue keys and schedule.
The existing DST, replay, authorization, history and transaction suites remain required.
`test_backup_operator.py` covers env-file inclusion, frozen Docker input, token exclusion,
three-file drift rejection and native offline SQLite backup with full-table comparison.
Earlier execution counts above are historical acceptance records, not current gate claims.
