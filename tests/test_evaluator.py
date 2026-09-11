"""
test_evaluator.py — Tests for experiment/evaluate.py (§16 acceptance gates).

Covers:
- Generation 1 vs first accepted patch (never borrow from generation 2-4)
- Early progress then regression
- Fourth-turn success without a final report
- verified_progress_at_snapshot logic
- ever_verified_progress aggregation (True/False/null)
- task_checks_pass conditions
- Oracle unavailability propagation
- Introduced error detection (multiset comparison)
- Defect count tracking
"""
from __future__ import annotations

import pytest

from experiment.evaluate import (
    DefectCheckResult,
    EpisodeOutcome,
    SnapshotState,
    detect_introduced_errors,
)
from experiment.diagnostics import DiagnosticRecord, ParsedDiagnostics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_snapshot(
    episode_id: str = "ep_00",
    generation: int = 1,
    mypy_status: str = "pass",
    runtime_status: str = "pass",
    defect_checks: list[tuple[str, str]] | None = None,
    confirmed_workaround: bool = False,
    ambiguous_workaround: bool = False,
    introduced_error_status: str = "none_detected",
    protected_files_intact: bool | None = True,
) -> SnapshotState:
    snap = SnapshotState(
        snapshot_id=f"gen_{generation}_post_patch",
        episode_id=episode_id,
        generation_number=generation,
        mypy_status=mypy_status,
        mypy_error_count=0 if mypy_status == "pass" else 2,
        runtime_status=runtime_status,
        confirmed_workaround=confirmed_workaround,
        ambiguous_workaround=ambiguous_workaround,
        introduced_error_status=introduced_error_status,
        protected_files_intact=protected_files_intact,
    )
    if defect_checks:
        snap.defect_checks = [DefectCheckResult(d, r) for d, r in defect_checks]
    return snap


def compute_snap(snap: SnapshotState, baseline_defect_ids: list[str] | None = None) -> SnapshotState:
    snap.compute_aggregates(baseline_defect_ids or [d.defect_id for d in snap.defect_checks])
    return snap


# ---------------------------------------------------------------------------
# SnapshotState tests
# ---------------------------------------------------------------------------

class TestSnapshotState:
    def test_solution_agnostic_completion_ignores_reference_ast_checks(self):
        snap = make_snapshot(defect_checks=[("prescribed_shape", "fail")])
        snap.compute_aggregates(12)
        assert snap.task_checks_pass is True
        assert snap.defects_repaired == 12

    def test_solution_agnostic_partial_progress_uses_error_count(self):
        snap = make_snapshot(mypy_status="fail")
        snap.mypy_error_count = 7
        snap.compute_aggregates(12)
        assert snap.task_checks_pass is False
        assert snap.verified_progress_at_snapshot is True
        assert snap.defects_repaired == 5

    def test_no_progress_when_error_count_does_not_fall(self):
        snap = make_snapshot(mypy_status="fail")
        snap.mypy_error_count = 12
        snap.compute_aggregates(12)
        assert snap.verified_progress_at_snapshot is False

    def test_task_checks_pass_all_good(self):
        snap = make_snapshot(
            defect_checks=[("d1", "pass")],
        )
        compute_snap(snap, ["d1"])
        assert snap.task_checks_pass is True

    def test_task_checks_pass_false_if_mypy_fail(self):
        snap = make_snapshot(
            mypy_status="fail",
            defect_checks=[("d1", "pass")],
        )
        compute_snap(snap, ["d1"])
        assert snap.task_checks_pass is False

    def test_task_checks_pass_false_if_runtime_fail(self):
        snap = make_snapshot(
            runtime_status="fail",
            defect_checks=[("d1", "pass")],
        )
        compute_snap(snap, ["d1"])
        assert snap.task_checks_pass is False

    def test_task_checks_pass_false_if_workaround(self):
        snap = make_snapshot(
            defect_checks=[("d1", "pass")],
            confirmed_workaround=True,
        )
        compute_snap(snap, ["d1"])
        assert snap.task_checks_pass is False

    def test_task_checks_pass_none_if_oracle_unavailable(self):
        snap = make_snapshot(
            defect_checks=[("d1", "unavailable")],
        )
        compute_snap(snap, ["d1"])
        assert snap.task_checks_pass is None

    def test_verified_progress_true_one_defect_repaired(self):
        snap = make_snapshot(
            defect_checks=[("d1", "pass"), ("d2", "fail")],
        )
        compute_snap(snap, ["d1", "d2"])
        assert snap.verified_progress_at_snapshot is True
        assert snap.defects_repaired == 1

    def test_verified_progress_false_no_defects_repaired(self):
        snap = make_snapshot(
            defect_checks=[("d1", "fail"), ("d2", "fail")],
        )
        compute_snap(snap, ["d1", "d2"])
        assert snap.verified_progress_at_snapshot is False

    def test_verified_progress_false_runtime_fails(self):
        snap = make_snapshot(
            runtime_status="fail",
            defect_checks=[("d1", "pass")],
        )
        compute_snap(snap, ["d1"])
        assert snap.verified_progress_at_snapshot is False

    def test_verified_progress_false_if_workaround(self):
        snap = make_snapshot(
            defect_checks=[("d1", "pass")],
            ambiguous_workaround=True,
        )
        compute_snap(snap, ["d1"])
        assert snap.verified_progress_at_snapshot is False

    def test_verified_progress_none_if_introduced_error_detected(self):
        snap = make_snapshot(
            defect_checks=[("d1", "pass")],
            introduced_error_status="detected",
        )
        compute_snap(snap, ["d1"])
        assert snap.verified_progress_at_snapshot is None

    def test_verified_progress_none_if_oracle_unavailable_on_repaired(self):
        snap = make_snapshot(
            defect_checks=[("d1", "unavailable")],
        )
        compute_snap(snap, ["d1"])
        assert snap.verified_progress_at_snapshot is None

    def test_defects_repaired_count(self):
        snap = make_snapshot(
            defect_checks=[("d1", "pass"), ("d2", "pass"), ("d3", "fail")],
        )
        compute_snap(snap, ["d1", "d2", "d3"])
        assert snap.defects_repaired == 2

    def test_max_defects_not_computed_in_snapshot(self):
        """max_defects_repaired is an episode-level aggregate, not snapshot-level."""
        snap = make_snapshot(defect_checks=[("d1", "pass")])
        compute_snap(snap, ["d1"])
        # defects_repaired exists
        assert snap.defects_repaired is not None


# ---------------------------------------------------------------------------
# EpisodeOutcome tests
# ---------------------------------------------------------------------------

class TestEpisodeOutcome:
    def test_ever_verified_progress_true_if_any_snap_true(self):
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        snap1 = make_snapshot(generation=1, defect_checks=[("d1", "pass")])
        compute_snap(snap1, ["d1"])
        snap2 = make_snapshot(generation=2, defect_checks=[("d1", "fail")])  # regression
        compute_snap(snap2, ["d1"])
        outcome.snapshots = [snap1, snap2]
        outcome.compute_ever_verified_progress()
        assert outcome.ever_verified_progress is True  # snap1 qualifies

    def test_ever_verified_progress_false_all_fail(self):
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        snap1 = make_snapshot(generation=1, defect_checks=[("d1", "fail")])
        compute_snap(snap1, ["d1"])
        outcome.snapshots = [snap1]
        outcome.compute_ever_verified_progress()
        assert outcome.ever_verified_progress is False

    def test_ever_verified_progress_null_if_ambiguous(self):
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        snap1 = make_snapshot(generation=1, defect_checks=[("d1", "unavailable")])
        compute_snap(snap1, ["d1"])
        outcome.snapshots = [snap1]
        outcome.compute_ever_verified_progress()
        assert outcome.ever_verified_progress is None

    def test_ever_verified_progress_false_no_snapshots_on_normal_completion(self):
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        outcome.compute_ever_verified_progress()
        assert outcome.ever_verified_progress is False

    @pytest.mark.parametrize(
        "flag",
        ["technical_failure", "token_censored", "budget_censored",
         "request_outcome_unknown", "model_identity_failure"],
    )
    def test_ever_verified_progress_null_without_snapshots_when_indeterminate(self, flag):
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        setattr(outcome, flag, True)
        outcome.compute_ever_verified_progress()
        assert outcome.ever_verified_progress is None

    def test_first_turn_patch_is_gen1_not_gen2(self):
        """first_turn_verified_progress must reflect generation 1 only (§12)."""
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        # Gen 1 accepted but made no progress
        snap1 = make_snapshot(generation=1, defect_checks=[("d1", "fail")])
        compute_snap(snap1, ["d1"])
        # Gen 2 made progress (should NOT affect first_turn_verified_progress)
        snap2 = make_snapshot(generation=2, defect_checks=[("d1", "pass")])
        compute_snap(snap2, ["d1"])

        outcome.snapshots = [snap1, snap2]
        outcome.first_turn_patch_submitted = True
        outcome.first_turn_patch_accepted = True
        outcome.first_turn_verified_progress = snap1.verified_progress_at_snapshot

        outcome.compute_ever_verified_progress()
        outcome.compute_final_state()

        assert outcome.first_turn_verified_progress is False
        assert outcome.ever_verified_progress is True  # gen2 qualifies

    def test_regression_does_not_erase_ever_progress(self):
        """Early progress then regression: ever_verified_progress stays True."""
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        snap1 = make_snapshot(generation=1, defect_checks=[("d1", "pass")])  # progress
        compute_snap(snap1, ["d1"])
        snap2 = make_snapshot(generation=2, defect_checks=[("d1", "fail")])  # regression
        compute_snap(snap2, ["d1"])
        outcome.snapshots = [snap1, snap2]
        outcome.compute_ever_verified_progress()
        outcome.compute_final_state()
        assert outcome.ever_verified_progress is True
        assert outcome.final_verified_progress is False  # last state regressed

    def test_third_turn_success_no_final_report(self):
        """Third-generation tool success counts without a fourth final report."""
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        snap = make_snapshot(generation=3, defect_checks=[("d1", "pass")])
        compute_snap(snap, ["d1"])
        outcome.snapshots = [snap]
        outcome.generation_cap_reached = True
        outcome.final_report_observed = False
        outcome.compute_ever_verified_progress()
        outcome.compute_final_state()
        assert outcome.successful_completion is True
        assert outcome.final_report_observed is False

    def test_max_defects_repaired_tracks_peak(self):
        """max_defects_repaired reflects peak even after regression."""
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        snap1 = make_snapshot(generation=1, defect_checks=[("d1", "pass"), ("d2", "pass"), ("d3", "fail")])
        compute_snap(snap1, ["d1", "d2", "d3"])
        snap2 = make_snapshot(generation=2, defect_checks=[("d1", "pass"), ("d2", "fail"), ("d3", "fail")])
        compute_snap(snap2, ["d1", "d2", "d3"])
        outcome.snapshots = [snap1, snap2]
        outcome.compute_final_state()
        assert outcome.max_defects_repaired == 2
        assert outcome.final_defects_repaired == 1

    def test_forbidden_attempt_does_not_poison_later_valid(self):
        """forbidden_attempt_observed stays separate from valid progress (§12)."""
        outcome = EpisodeOutcome(episode_id="ep_00", fixture_id="f1_shared", condition="expanded")
        outcome.forbidden_attempt_observed = True  # a rejection was logged
        snap1 = make_snapshot(generation=2, defect_checks=[("d1", "pass")])  # later valid
        compute_snap(snap1, ["d1"])
        outcome.snapshots = [snap1]
        outcome.compute_ever_verified_progress()
        assert outcome.ever_verified_progress is True
        assert outcome.forbidden_attempt_observed is True  # separate flag preserved


# ---------------------------------------------------------------------------
# Introduced error detection
# ---------------------------------------------------------------------------

class TestIntroducedErrors:
    def _make_diag_records(self, items: list[tuple[str, str | None, str]]) -> list[DiagnosticRecord]:
        """items = list of (file, code, message)"""
        return [
            DiagnosticRecord(
                file=f, line=i, col=1, severity="error",
                message=msg, code=code, occurrence_index=i,
            )
            for i, (f, code, msg) in enumerate(items)
        ]

    def test_no_new_errors(self):
        baseline = self._make_diag_records([
            ("a.py", "return-value", "Bad return"),
        ])
        current = self._make_diag_records([])
        status, records = detect_introduced_errors(baseline, current)
        assert status == "none_detected"
        assert records == []

    def test_new_error_detected(self):
        baseline = self._make_diag_records([
            ("a.py", "return-value", "Bad return"),
        ])
        current = self._make_diag_records([
            ("a.py", "return-value", "Bad return"),
            ("b.py", "arg-type", "New introduced error"),
        ])
        status, records = detect_introduced_errors(baseline, current)
        assert status == "detected"
        assert len(records) == 1
        assert records[0]["file"] == "b.py"

    def test_more_occurrences_detected(self):
        """Same error at more locations than baseline = new introduction."""
        baseline = self._make_diag_records([
            ("a.py", "return-value", "Bad return"),
        ])
        current = self._make_diag_records([
            ("a.py", "return-value", "Bad return"),
            ("b.py", "return-value", "Bad return"),  # additional occurrence
        ])
        status, records = detect_introduced_errors(baseline, current)
        assert status == "detected"

    def test_fewer_errors_not_flagged(self):
        """Fewer errors than baseline is fine (we only detect introductions)."""
        baseline = self._make_diag_records([
            ("a.py", "return-value", "Bad return"),
            ("b.py", "arg-type", "Arg mismatch"),
        ])
        current = self._make_diag_records([
            ("a.py", "return-value", "Bad return"),
        ])
        status, _ = detect_introduced_errors(baseline, current)
        assert status == "none_detected"

    def test_notes_not_counted_as_errors(self):
        """Note records are not counted in the error multiset."""
        baseline = self._make_diag_records([])
        note = DiagnosticRecord(
            file="a.py", line=1, col=1, severity="note",
            message="context note", code=None, occurrence_index=0,
        )
        status, _ = detect_introduced_errors(baseline, [note])
        assert status == "none_detected"
