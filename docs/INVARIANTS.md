# First-slice invariant ledger

Evidence is synthetic and local to the first-slice worktree. No live bot calls,
production records, production migration, deployment or release approval are implied.

| Invariant | Implementation boundary | Regression evidence |
| --- | --- | --- |
| Owner AND exact chat/topic AND stored prompt destination | Store.select, Application command handler | test_authorization_invalid_atomic; test_wrong_identity_neither_writes_nor_exports (including missing user); test_destination_change_does_not_send_old_catalogue |
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

## Execution record

RED before implementation: `python3 -m unittest discover -s tests -v` failed with
`ModuleNotFoundError: No module named 'tapkeeper'`. Subsequent edge RED runs showed
wrong-destination sending, None callback exception, wrong-message acceptance, and
first uncertain visible prompt rejection; fixes were followed by passing tests.
Tests use unittest lifecycle fixtures under pytest; pytest is the canonical runner.

Latest full commands actually executed:

```sh
uv sync --frozen
uv run --frozen pytest
uv run --frozen --python 3.12 pytest
uv run --frozen ruff check tapkeeper tests
uv build
uv run --isolated --no-project --with ./dist/tapkeeper-0.1.0-py3-none-any.whl python -I -c 'import tapkeeper, telegram; from tapkeeper.cli import main; print("installed wheel imports OK; PTB", telegram.__version__)'
git diff --check
```

Full suite: **29 passed** on Python **3.13.13** and **3.12.3**. Ruff clean; source
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
