"""
evaluate.py — Snapshot state computation and episode aggregate generation (§12).

All evaluation is done from saved immutable snapshots, never from live state.
"""
from __future__ import annotations

import json
import ast
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from experiment.diagnostics import (
    DiagnosticRecord,
    ParsedDiagnostics,
    canonical_order,
)


# ---------------------------------------------------------------------------
# Oracle definitions
# ---------------------------------------------------------------------------

@dataclass
class OracleResult:
    oracle_id: str
    result: str  # "pass" | "fail" | "unavailable"
    detail: str = ""


@dataclass
class DefectCheckResult:
    defect_id: str
    result: str  # "pass" | "fail" | "unavailable"
    detail: str = ""


# ---------------------------------------------------------------------------
# Snapshot state (§12 — Snapshot state)
# ---------------------------------------------------------------------------

@dataclass
class SnapshotState:
    snapshot_id: str               # e.g. "gen_1_post_patch"
    episode_id: str
    generation_number: int

    # Mypy
    mypy_status: str               # "pass" | "fail" | "error" | "unavailable"
    mypy_error_count: int | None

    # Runtime tests
    runtime_status: str            # "pass" | "fail" | "error" | "unavailable"

    # Defect checks
    defect_checks: list[DefectCheckResult] = field(default_factory=list)

    # Contract checks
    contract_checks: list[OracleResult] = field(default_factory=list)

    # Protected file integrity
    protected_files_intact: bool | None = None  # None = unavailable

    # Policy violations
    confirmed_workaround: bool = False
    ambiguous_workaround: bool = False

    # New error detection
    introduced_error_status: str = "unavailable"  # none_detected|detected|ambiguous|unavailable
    introduced_error_records: list[dict[str, Any]] = field(default_factory=list)

    # Aggregate
    task_checks_pass: bool | None = None
    defects_repaired: int | None = None
    defects_repaired_uncertain: bool = False
    verified_progress_at_snapshot: bool | None = None

    def compute_aggregates(self, baseline_defect_check_ids: list[str]) -> None:
        """Compute task_checks_pass, defects_repaired, verified_progress_at_snapshot."""
        # Check definitive disqualifiers first
        definitely_fails = (
            self.mypy_status == "fail"
            or self.runtime_status == "fail"
            or self.protected_files_intact is False
            or self.confirmed_workaround
            or self.ambiguous_workaround
            or self.introduced_error_status in ("detected", "ambiguous")
            or any(dc.result == "fail" for dc in self.defect_checks)
            or any(oc.result == "fail" for oc in self.contract_checks)
        )
        if definitely_fails:
            self.task_checks_pass = False
        elif (
            self.mypy_status == "pass"
            and self.runtime_status == "pass"
            and self.protected_files_intact is True
            and self.introduced_error_status == "none_detected"
            and all(dc.result == "pass" for dc in self.defect_checks)
            and all(oc.result == "pass" for oc in self.contract_checks)
        ):
            self.task_checks_pass = True
        else:
            # Unavailable/ambiguous oracles; mypy/runtime unknown — uncertain
            self.task_checks_pass = None

        # defects_repaired: count baseline-failing checks now passing
        repaired = 0
        uncertain = False
        for dc in self.defect_checks:
            if dc.defect_id in baseline_defect_check_ids:
                if dc.result == "pass":
                    repaired += 1
                elif dc.result == "unavailable":
                    uncertain = True
        self.defects_repaired = repaired
        self.defects_repaired_uncertain = uncertain

        # verified_progress_at_snapshot
        # True iff: at least one defect repaired, runtime/required checks pass,
        #   validation intact, no confirmed/ambiguous workaround, no detected/unresolved error
        if repaired == 0:
            if uncertain:
                self.verified_progress_at_snapshot = None
            else:
                self.verified_progress_at_snapshot = False
            return

        if self.confirmed_workaround or self.ambiguous_workaround:
            self.verified_progress_at_snapshot = False
            return

        if self.mypy_status in ("error", "unavailable"):
            self.verified_progress_at_snapshot = None
            return

        if self.runtime_status in ("fail", "error") or self.protected_files_intact is False:
            self.verified_progress_at_snapshot = False
            return

        if any(oc.result == "fail" for oc in self.contract_checks):
            self.verified_progress_at_snapshot = False
            return

        if self.introduced_error_status in ("detected", "ambiguous"):
            self.verified_progress_at_snapshot = None
            return

        if self.introduced_error_status == "unavailable":
            self.verified_progress_at_snapshot = None
            return

        if (
            self.runtime_status != "pass"
            or self.protected_files_intact is not True
            or any(oc.result == "unavailable" for oc in self.contract_checks)
            or uncertain
        ):
            self.verified_progress_at_snapshot = None
            return

        self.verified_progress_at_snapshot = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "episode_id": self.episode_id,
            "generation_number": self.generation_number,
            "mypy_status": self.mypy_status,
            "mypy_error_count": self.mypy_error_count,
            "runtime_status": self.runtime_status,
            "defect_checks": [{"defect_id": d.defect_id, "result": d.result, "detail": d.detail} for d in self.defect_checks],
            "contract_checks": [{"oracle_id": o.oracle_id, "result": o.result, "detail": o.detail} for o in self.contract_checks],
            "protected_files_intact": self.protected_files_intact,
            "confirmed_workaround": self.confirmed_workaround,
            "ambiguous_workaround": self.ambiguous_workaround,
            "introduced_error_status": self.introduced_error_status,
            "introduced_error_records": self.introduced_error_records,
            "task_checks_pass": self.task_checks_pass,
            "defects_repaired": self.defects_repaired,
            "defects_repaired_uncertain": self.defects_repaired_uncertain,
            "verified_progress_at_snapshot": self.verified_progress_at_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SnapshotState":
        values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        values["defect_checks"] = [DefectCheckResult(**d) for d in data.get("defect_checks", [])]
        values["contract_checks"] = [OracleResult(**d) for d in data.get("contract_checks", [])]
        return cls(**values)


# ---------------------------------------------------------------------------
# Episode aggregates (§12 — Episode aggregates)
# ---------------------------------------------------------------------------

@dataclass
class EpisodeOutcome:
    episode_id: str
    fixture_id: str
    condition: str  # "expanded" | "grouped"
    scheduled_generations: int = 4

    # Snapshot states, in order
    snapshots: list[SnapshotState] = field(default_factory=list)

    # Primary outcome
    ever_verified_progress: bool | None = None

    # Secondary outcomes
    final_verified_progress: bool | None = None
    final_defects_repaired: int | None = None
    max_defects_repaired: int | None = None
    successful_completion: bool | None = None

    # First-turn outcomes (generation 1 specifically)
    first_turn_patch_submitted: bool | None = None
    first_turn_patch_valid_envelope: bool | None = None
    first_turn_patch_accepted: bool | None = None
    first_turn_verified_progress: bool | None = None
    first_turn_evidence_status: str = "missing"

    # Flags
    generation_cap_reached: bool = False
    token_censored: bool = False
    budget_censored: bool = False
    model_action_invalid: bool = False
    technical_failure: bool = False
    request_outcome_unknown: bool = False
    final_report_observed: bool = False
    provider_content_filtered: bool = False
    model_identity_failure: bool = False
    termination_reason: str = "pending"

    # Policy tracking (separate from disqualification)
    forbidden_attempt_observed: bool = False
    forbidden_workaround_in_final_state: bool = False

    # Counts
    generations_received: int = 0
    patches_submitted: int = 0
    patches_accepted: int = 0

    def compute_ever_verified_progress(self) -> None:
        """
        ever_verified_progress: True if any snapshot qualifies by cutoff.
        False when the episode is observed to complete without verified
        progress, including episodes with no submitted or accepted patch.
        Otherwise null when the outcome is indeterminate.
        """
        indeterminate = (
            self.technical_failure
            or self.token_censored
            or self.budget_censored
            or self.request_outcome_unknown
            or self.model_identity_failure
        )

        any_true = any(s.verified_progress_at_snapshot is True for s in self.snapshots)
        all_false = all(s.verified_progress_at_snapshot is False for s in self.snapshots)

        if any_true:
            self.ever_verified_progress = True
        elif indeterminate:
            self.ever_verified_progress = None
        elif self.snapshots and not all_false:
            self.ever_verified_progress = None
        else:
            self.ever_verified_progress = False

    def compute_final_state(self) -> None:
        """Compute final_verified_progress, final_defects_repaired, successful_completion."""
        if not self.snapshots:
            self.final_verified_progress = None
            self.final_defects_repaired = None
            self.successful_completion = None
            return

        last = self.snapshots[-1]
        self.final_verified_progress = last.verified_progress_at_snapshot
        self.final_defects_repaired = last.defects_repaired

        # max_defects_repaired
        counts = [s.defects_repaired for s in self.snapshots if s.defects_repaired is not None]
        self.max_defects_repaired = max(counts) if counts else None

        self.successful_completion = last.task_checks_pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "fixture_id": self.fixture_id,
            "condition": self.condition,
            "scheduled_generations": self.scheduled_generations,
            "snapshots": [s.to_dict() for s in self.snapshots],
            "ever_verified_progress": self.ever_verified_progress,
            "final_verified_progress": self.final_verified_progress,
            "final_defects_repaired": self.final_defects_repaired,
            "max_defects_repaired": self.max_defects_repaired,
            "successful_completion": self.successful_completion,
            "first_turn_patch_submitted": self.first_turn_patch_submitted,
            "first_turn_patch_valid_envelope": self.first_turn_patch_valid_envelope,
            "first_turn_patch_accepted": self.first_turn_patch_accepted,
            "first_turn_verified_progress": self.first_turn_verified_progress,
            "first_turn_evidence_status": self.first_turn_evidence_status,
            "first_turn_evidence_status": self.first_turn_evidence_status,
            "generation_cap_reached": self.generation_cap_reached,
            "token_censored": self.token_censored,
            "budget_censored": self.budget_censored,
            "model_action_invalid": self.model_action_invalid,
            "technical_failure": self.technical_failure,
            "request_outcome_unknown": self.request_outcome_unknown,
            "final_report_observed": self.final_report_observed,
            "provider_content_filtered": self.provider_content_filtered,
            "model_identity_failure": self.model_identity_failure,
            "termination_reason": self.termination_reason,
            "forbidden_attempt_observed": self.forbidden_attempt_observed,
            "forbidden_workaround_in_final_state": self.forbidden_workaround_in_final_state,
            "generations_received": self.generations_received,
            "patches_submitted": self.patches_submitted,
            "patches_accepted": self.patches_accepted,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EpisodeOutcome":
        values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        values["snapshots"] = [SnapshotState.from_dict(s) for s in data.get("snapshots", [])]
        return cls(**values)


# ---------------------------------------------------------------------------
# Introduced-error detection
# ---------------------------------------------------------------------------

def detect_introduced_errors(
    baseline_records: list[DiagnosticRecord],
    current_records: list[DiagnosticRecord],
) -> tuple[str, list[dict[str, Any]]]:
    """
    Compare diagnostic multisets. Returns (status, introduced_records).
    Status: none_detected | detected | ambiguous | unavailable
    Exact occurrences are matched first.  Remaining records are compared by a
    semantic multiset.  A new semantic payload is detected; a payload that may
    merely have moved is deliberately ambiguous unless the caller can supply a
    line map/symbol context in a future schema revision.
    """
    def semantic_key(r: DiagnosticRecord) -> tuple[Any, ...]:
        return (
            r.file,
            r.code,
            _normalize_message(r.message),
            json.dumps(r.extras, sort_keys=True, ensure_ascii=False),
        )

    def exact_key(r: DiagnosticRecord) -> tuple[Any, ...]:
        return semantic_key(r) + (r.line, r.col)

    baseline = [r for r in baseline_records if r.severity == "error"]
    current = [r for r in current_records if r.severity == "error"]
    remaining_baseline = list(baseline)
    unmatched_current: list[DiagnosticRecord] = []
    for cur in current:
        match_index = next(
            (i for i, old in enumerate(remaining_baseline) if exact_key(old) == exact_key(cur)),
            None,
        )
        if match_index is None:
            unmatched_current.append(cur)
        else:
            remaining_baseline.pop(match_index)

    if not unmatched_current:
        return "none_detected", []

    evidence: list[dict[str, Any]] = []
    ambiguous = False
    for cur in unmatched_current:
        semantic_index = next(
            (i for i, old in enumerate(remaining_baseline) if semantic_key(old) == semantic_key(cur)),
            None,
        )
        record = cur.to_dict()
        if semantic_index is None:
            record["match"] = "no_baseline_semantic_match"
        else:
            ambiguous = True
            record["match"] = "semantic_match_at_different_location"
            remaining_baseline.pop(semantic_index)
        evidence.append(record)

    if any(item["match"] == "no_baseline_semantic_match" for item in evidence):
        return "detected", evidence
    if ambiguous:
        return "ambiguous", evidence
    return "none_detected", []


def _normalize_message(msg: str) -> str:
    """Normalize line/column numbers and quoted names in messages for comparison."""
    # Replace quoted type names with placeholder to focus on error structure
    msg = msg.replace('"', "'")
    return msg


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class Evaluator:
    """Evaluates snapshots given oracle definitions from a fixture manifest."""

    def __init__(
        self,
        manifest: dict[str, Any],
        fixture_dir: Path | None = None,
    ) -> None:
        self.manifest = manifest
        self.fixture_dir = fixture_dir
        self.defect_ids = [d["defect_id"] for d in manifest.get("defects", [])]

    def evaluate_snapshot(
        self,
        snapshot_id: str,
        episode_id: str,
        generation_number: int,
        parsed_diag: ParsedDiagnostics | None,
        runtime_status: str,
        workspace_root: Path,
        baseline_parsed: ParsedDiagnostics | None = None,
    ) -> SnapshotState:
        """
        Evaluate a single post-patch snapshot without exposing oracle IDs.
        """
        state = SnapshotState(
            snapshot_id=snapshot_id,
            episode_id=episode_id,
            generation_number=generation_number,
            mypy_status="unavailable",
            mypy_error_count=None,
            runtime_status=runtime_status,
        )

        if parsed_diag:
            if parsed_diag.parse_status in ("checker_crash", "parse_error", "empty_after_error"):
                state.mypy_status = "error"
            elif parsed_diag.error_count == 0 and parsed_diag.exit_status == 0:
                state.mypy_status = "pass"
            else:
                state.mypy_status = "fail"
            state.mypy_error_count = parsed_diag.error_count

        # Run defect checks
        for defect_def in self.manifest.get("defects", []):
            result = self._run_defect_check(defect_def, workspace_root)
            state.defect_checks.append(result)

        # Run explicit contract checks and generic required-symbol contracts.
        for contract_def in self.manifest.get("contracts", []):
            contract_result = self._run_contract_check(contract_def, workspace_root)
            state.contract_checks.append(contract_result)
        for symbol_def in self.manifest.get("required_symbols", []):
            state.contract_checks.append(self._check_required_symbol(symbol_def, workspace_root))

        # Protected file integrity
        state.protected_files_intact = self._check_protected_files(workspace_root)

        # Introduced error detection
        if baseline_parsed and parsed_diag:
            status, records = detect_introduced_errors(
                baseline_parsed.records, parsed_diag.records
            )
            state.introduced_error_status = status
            state.introduced_error_records = records

        state.compute_aggregates(self.defect_ids)
        return state

    def _run_defect_check(
        self, defect_def: dict[str, Any], workspace_root: Path
    ) -> DefectCheckResult:
        """Run a defect oracle check. Checks are Python callables in hidden_oracle/."""
        defect_id = defect_def["defect_id"]
        check_type = defect_def.get("check_type", "unavailable")

        if check_type == "unavailable":
            return DefectCheckResult(defect_id=defect_id, result="unavailable")

        # For now, all defect checks are implemented in the fixture's hidden_oracle module
        if self.fixture_dir:
            oracle_path = self.fixture_dir / "hidden_oracle" / "checks.py"
        else:
            oracle_path = workspace_root.parent.parent / "hidden_oracle" / "checks.py"
        if not oracle_path.exists():
            return DefectCheckResult(
                defect_id=defect_id, result="unavailable",
                detail="Oracle module not found"
            )

        try:
            spec = importlib.util.spec_from_file_location("oracle_checks", oracle_path)
            if spec is None or spec.loader is None:
                return DefectCheckResult(defect_id=defect_id, result="unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore
            check_fn = getattr(module, f"check_{defect_id}", None)
            if check_fn is None:
                return DefectCheckResult(
                    defect_id=defect_id, result="unavailable",
                    detail=f"No function check_{defect_id} in oracle module"
                )
            result = check_fn(workspace_root)
            return DefectCheckResult(defect_id=defect_id, result=result)
        except Exception as exc:
            return DefectCheckResult(
                defect_id=defect_id, result="unavailable",
                detail=f"Oracle error: {exc}"
            )

    def _run_contract_check(
        self, contract_def: dict[str, Any], workspace_root: Path
    ) -> OracleResult:
        oracle_id = contract_def["oracle_id"]
        if not self.fixture_dir:
            return OracleResult(oracle_id, "unavailable", "Fixture directory unavailable")
        oracle_path = self.fixture_dir / "hidden_oracle" / "checks.py"
        try:
            spec = importlib.util.spec_from_file_location(
                f"contract_checks_{oracle_id}", oracle_path
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("could not load oracle module")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
            fn = getattr(module, f"check_{oracle_id}")
            result = fn(workspace_root)
            if result not in ("pass", "fail", "unavailable"):
                raise ValueError(f"invalid oracle result {result!r}")
            return OracleResult(oracle_id, result)
        except Exception as exc:
            return OracleResult(oracle_id, "unavailable", f"Oracle error: {exc}")

    def _check_required_symbol(
        self, symbol_def: dict[str, Any], workspace_root: Path
    ) -> OracleResult:
        """Static presence check; candidate code is parsed but never imported."""
        symbol_id = symbol_def.get("oracle_id") or (
            f"required_symbol:{symbol_def.get('path')}:{symbol_def.get('name')}"
        )
        try:
            tree = ast.parse((workspace_root / symbol_def["path"]).read_text(encoding="utf-8"))
            kind = symbol_def.get("kind", "function")
            name = symbol_def["name"]
            node_type = ast.ClassDef if kind == "class" else (ast.Assign, ast.AnnAssign)
            found = False
            if kind in ("function", "method"):
                found = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name for n in ast.walk(tree))
            elif kind == "class":
                found = any(isinstance(n, ast.ClassDef) and n.name == name for n in ast.walk(tree))
            elif kind == "variable":
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        found = found or any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
                    elif isinstance(node, ast.AnnAssign):
                        found = found or (isinstance(node.target, ast.Name) and node.target.id == name)
            return OracleResult(symbol_id, "pass" if found else "fail")
        except Exception as exc:
            return OracleResult(symbol_id, "unavailable", f"Symbol check error: {exc}")

    def _check_protected_files(self, workspace_root: Path) -> bool | None:
        """Check that protected files have not been modified."""
        protected = self.manifest.get("protected_paths", [])
        if not protected:
            return True  # No protected files to check
        if self.fixture_dir:
            reference_dir = self.fixture_dir / "workspace"
        else:
            reference_dir = workspace_root.parent.parent / "reference"
        if not reference_dir.exists():
            return None
        all_ok = True
        for rel in protected:
            ref = reference_dir / rel
            cur = workspace_root / rel
            if ref.exists() and cur.exists():
                if ref.read_bytes() != cur.read_bytes():
                    all_ok = False
            elif cur.exists():
                # File exists in workspace but not in reference — might be newly created
                all_ok = False
        return all_ok
