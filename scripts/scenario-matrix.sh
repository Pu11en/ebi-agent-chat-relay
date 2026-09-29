#!/usr/bin/env bash
# Run the stabilize-session-reliability scenario matrix (design.md section 6).
# Offline only: temporary SQLite/Git, fake Discord and backends, no model calls.
# Usage: scripts/scenario-matrix.sh   (each row prints its own pytest summary)
set -uo pipefail
cd "$(dirname "$0")/.."

I=tests/test_event_session_integrity.py
W=tests/test_worker_sessions.py
L=tests/test_worker_lifecycle_integrity.py
C=tests/test_task_loop_cog.py
V=tests/test_voice_tag_integrity.py

declare -a ROWS=(
  "A->B->next reply->reload|$I::test_backend_switch_next_reply_and_reload_resume_the_new_binding"
  "bad resume / stale result|$I::test_rejected_resume_cannot_claim_another_backends_id $I::test_rejected_echo_after_init_preserves_verified_binding $I::test_late_result_from_an_evicted_run_cannot_replace_a_newer_binding $I::test_resumed_run_may_advance_its_own_binding"
  "close->Discord failure->restart->retry|tests/test_archive_recovery.py $L::test_whole_build_archive_failure_converges_after_process_reconstruction tests/test_lifecycle_wiring.py::TestRestartAndReopen"
  "worker spawn vs allocation|$W::test_worker_create_event_cannot_take_a_tag_before_registration $W::test_failed_worker_exclusion_cannot_leave_a_tagged_orphan"
  "completion->DB/Discord failure->retry|$W::test_failed_worker_close_does_not_claim_archive_complete $W::test_failed_tag_exclusion_keeps_the_worker_owed_for_retry $W::test_retry_archives_counts_threads_not_tasks tests/gowork_upgrade/test_worker_archive_eligibility.py $L::test_integrated_legacy_worker_closes_only_when_the_build_is_kept $L::test_thrown_away_build_leaves_landed_legacy_workers_open"
  "waiting/closed/ambiguous loop->repeated restart|$C::TestResume::test_explicitly_closed_build_is_not_restarted $C::TestResume::test_restart_restores_user_wait_without_another_model_turn $C::TestLegacyLoopRecovery"
  "all tags legitimately used|tests/test_voice_labels.py::test_more_threads_than_tags_leaves_the_remainder_untagged tests/test_voice_labels.py::test_a_visible_thread_never_has_its_tag_taken tests/test_consistency_check.py::test_pool_exhaustion_is_not_reported_as_a_failure"
  "snapshot excludes stale holder|$V::test_paginated_api_never_reclaims_an_open_holder $V::test_closed_off_page_holder_is_released_without_a_new_allocation $V::test_reopened_holder_is_not_released_by_a_stale_closed_view"
  "assertions pass but a task crashes|tests/test_async_failure_gate.py"
)

status=0
for row in "${ROWS[@]}"; do
  name=${row%%|*}
  # shellcheck disable=SC2086 # intentional word splitting of test IDs
  summary=$(uv run pytest -q ${row#*|} 2>&1 | tail -1)
  case "$summary" in *failed*|*error*|*"no tests ran"*) status=1 ;; esac
  printf '%-48s %s\n' "$name" "$summary"
done
exit $status
