"""
runner.py — Episode runner implementing the 4-generation loop (§8, §14).

Handles:
- Calibration and discovery phases
- Mock mode (no API calls)
- Safe resume from persisted state
- Atomic per-boundary persistence
- Budget enforcement
- Full §8 response-handling decision table
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from experiment.groq_client import (
    AttemptRecord,
    BudgetState,
    GenerationRecord,
    GroqExperimentClient,
    _classify_response,
    estimate_tokens,
)
from experiment.tool import (
    TOOL_SCHEMA,
    WorkspaceConfig,
    format_feedback,
    parse_tool_arguments,
    patch_and_check,
    hash_workspace,
    apply_patch,
    validate_current_workspace,
    PatchResult,
)
from experiment.diagnostics import (
    ParsedDiagnostics,
    PathNormalizer,
    parse_mypy_json_output,
    render_expanded,
    render_grouped,
    verify_round_trip,
)
from experiment.evaluate import (
    Evaluator,
    EpisodeOutcome,
    SnapshotState,
)
from experiment.schedule import (
    load_schedule,
    CALIBRATION_FIXTURE_IDS,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_KEYS_FOR_MODEL = {
    "model", "temperature", "reasoning_effort", "max_completion_tokens",
    "include_reasoning", "tool_choice", "parallel_tool_calls", "stream",
}

MAX_GENERATIONS = 4
CAL_MAX_EPISODES = 8
DISC_EPISODES = 16


def _generation_from_dict(data: dict[str, Any]) -> GenerationRecord:
    values = {
        k: v for k, v in data.items()
        if k in GenerationRecord.__dataclass_fields__ and k != "attempts"
    }
    values["attempts"] = [AttemptRecord(**a) for a in data.get("attempts", [])]
    return GenerationRecord(**values)


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

def load_fixture(fixture_id: str, fixtures_root: Path) -> dict[str, Any]:
    """Load manifest.json for a fixture."""
    fixture_dir = fixtures_root / fixture_id
    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Fixture manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_public_files(workspace_root: Path, manifest: dict[str, Any]) -> str:
    """
    Load all public workspace files in deterministic order for prompt construction.
    Returns a formatted source block.
    """
    public_paths = manifest.get("public_paths", [])
    blocks: list[str] = []
    for rel in sorted(public_paths):
        full = workspace_root / rel
        if not full.exists():
            blocks.append(f"# FILE: {rel}\n[FILE NOT FOUND]\n")
            continue
        content = full.read_text(encoding="utf-8")
        blocks.append(f"# FILE: {rel}\n```\n{content}\n```\n")
    return "\n".join(blocks)


def build_prompt(
    prompt_template: str,
    report: str,
    source_block: str,
) -> str:
    return prompt_template.replace("{report}", report).replace("{source_block}", source_block)


def _predict_post_patch_hash(
    episode_dir: Path, generation: int, patch_text: str, allowlisted: list[str],
) -> str | None:
    """Apply a patch to the latest durable source snapshot, never the live tree."""
    candidates = sorted(
        (
            p for p in episode_dir.glob("gen_*_source.json")
            if int(p.stem.split("_")[1]) < generation
        ),
        key=lambda p: int(p.stem.split("_")[1]),
    )
    if not candidates:
        return None
    data = json.loads(candidates[-1].read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(dir=episode_dir) as temp_dir:
        root = Path(temp_dir)
        for rel, content in data.get("files", {}).items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", errors="surrogateescape")
        applied = apply_patch(root, patch_text, allowlisted)
        if applied["status"] != "applied":
            return None
        return hash_workspace(root, allowlisted)


# ---------------------------------------------------------------------------
# Episode persistence
# ---------------------------------------------------------------------------

def _atomic_replace(tmp_path: Path, target_path: Path) -> None:
    """Safely replace target_path with tmp_path, handling transient Windows file locks."""
    for attempt in range(6):
        try:
            os.replace(tmp_path, target_path)
            return
        except PermissionError:
            if attempt < 5:
                time.sleep(0.05)
            else:
                raise


class EpisodeLogger:
    """Append-only event log and atomic state persistence for one episode."""

    def __init__(self, episode_dir: Path) -> None:
        self.episode_dir = episode_dir
        episode_dir.mkdir(parents=True, exist_ok=True)
        self.event_log_path = episode_dir / "events.jsonl"
        self.outcome_path = episode_dir / "outcome.json"
        self.messages_path = episode_dir / "messages.json"
        self.state_path = episode_dir / "state.json"

    def append_event(self, event_type: str, data: dict[str, Any]) -> None:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "data": data,
        }
        with open(self.event_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def save_messages(self, messages: list[dict[str, Any]]) -> None:
        tmp = self.messages_path.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        tmp.write_text(json.dumps(messages, indent=2), encoding="utf-8")
        _atomic_replace(tmp, self.messages_path)

    def save_state(
        self, outcome: EpisodeOutcome, messages: list[dict[str, Any]],
        workspace_hash: str,
    ) -> None:
        payload = {
            "outcome": outcome.to_dict(),
            "messages": messages,
            "workspace_hash": workspace_hash,
        }
        tmp = self.state_path.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(tmp, self.state_path)

    def load_state(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def has_event(self, event_type: str, generation: int) -> bool:
        if not self.event_log_path.exists():
            return False
        for line in self.event_log_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                event.get("event_type") == event_type
                and event.get("data", {}).get("generation") == generation
            ):
                return True
        return False

    def save_patch(self, generation: int, patch: str) -> None:
        target = self.episode_dir / f"gen_{generation:02d}_patch.diff"
        tmp = target.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        with open(tmp, "w", encoding="utf-8", newline="") as handle:
            handle.write(patch)
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_replace(tmp, target)

    def save_source_snapshot(self, generation: int, workspace: Path) -> None:
        files: dict[str, str] = {}
        for path in sorted(p for p in workspace.rglob("*") if p.is_file() and not p.is_symlink()):
            rel = path.relative_to(workspace).as_posix()
            if any(part in {".pytest_cache", ".mypy_cache", "__pycache__"} for part in path.parts):
                continue
            files[rel] = path.read_text(encoding="utf-8", errors="surrogateescape")
        target = self.episode_dir / f"gen_{generation:02d}_source.json"
        tmp = target.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        tmp.write_text(json.dumps({"files": files}, indent=2), encoding="utf-8")
        _atomic_replace(tmp, target)

    def save_outcome(self, outcome: EpisodeOutcome) -> None:
        tmp = self.outcome_path.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        tmp.write_text(json.dumps(outcome.to_dict(), indent=2), encoding="utf-8")
        _atomic_replace(tmp, self.outcome_path)

    def is_completed(self) -> bool:
        return self.outcome_path.exists()

    def load_outcome(self) -> dict[str, Any] | None:
        if self.outcome_path.exists():
            return json.loads(self.outcome_path.read_text(encoding="utf-8"))
        return None

    def save_generation(self, gen: GenerationRecord) -> None:
        gen_path = self.episode_dir / f"gen_{gen.generation_number:02d}.json"
        tmp = gen_path.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        tmp.write_text(json.dumps(asdict(gen), indent=2), encoding="utf-8")
        _atomic_replace(tmp, gen_path)

    def save_tool_result(self, generation: int, result: dict[str, Any]) -> None:
        rp = self.episode_dir / f"gen_{generation:02d}_tool_result.json"
        tmp = rp.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        tmp.write_text(json.dumps(result, indent=2), encoding="utf-8")
        _atomic_replace(tmp, rp)

    def save_snapshot(self, generation: int, snapshot: SnapshotState) -> None:
        sp = self.episode_dir / f"gen_{generation:02d}_snapshot.json"
        tmp = sp.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        tmp.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
        _atomic_replace(tmp, sp)


# ---------------------------------------------------------------------------
# Mock client
# ---------------------------------------------------------------------------

class MockGroqClient:
    """
    Mock client for offline testing. Returns scripted responses.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self._call_index = 0
        self.model_id = "openai/gpt-oss-120b"

    def generate(
        self,
        episode_id: str,
        generation_number: int,
        messages: list[dict[str, Any]],
        tool_schema: dict[str, Any] | None = None,
        estimated_input_tokens: int | None = None,
    ) -> GenerationRecord:
        if self._call_index >= len(self._responses):
            rec = GenerationRecord(
                episode_id=episode_id,
                generation_number=generation_number,
                model_id_requested=self.model_id,
                model_id_returned=self.model_id,
                finish_reason="stop",
                termination_code="model_final",
                assistant_content="No more scripted responses.",
            )
            return rec

        resp = self._responses[self._call_index]
        self._call_index += 1

        rec = GenerationRecord(
            episode_id=episode_id,
            generation_number=generation_number,
            model_id_requested=self.model_id,
            model_id_returned=resp.get("model_id", self.model_id),
            finish_reason=resp.get("finish_reason", "stop"),
            termination_code="pending",
            assistant_content=resp.get("content"),
            reasoning_content=resp.get("reasoning"),
            tool_calls=resp.get("tool_calls", []),
            total_input_tokens=resp.get("input_tokens", 100),
            total_output_tokens=resp.get("output_tokens", 50),
        )
        rec.termination_code = _classify_response(rec)
        return rec


# ---------------------------------------------------------------------------
# Episode runner
# ---------------------------------------------------------------------------

def run_episode(
    episode_id: str,
    fixture_id: str,
    condition: str,
    fixtures_root: Path,
    episode_dir: Path,
    client: GroqExperimentClient | MockGroqClient,
    prompt_template: str,
    model_settings: dict[str, Any],
    budget: BudgetState | None = None,
    mock: bool = False,
    unsafe_local: bool = False,
    resume: bool = False,
    container_image: str | None = None,
    phase: str = "discovery",
) -> EpisodeOutcome:
    """Run a single episode with full §8 decision table and safe resume."""

    ep_log = EpisodeLogger(episode_dir)

    # Resume: skip completed episodes
    if ep_log.is_completed() and resume:
        logger.info("Episode %s already completed, skipping.", episode_id)
        stored = ep_log.load_outcome()
        if stored:
            return EpisodeOutcome.from_dict(stored)
        raise RuntimeError(f"Unreadable completed outcome for {episode_id}")

    if ep_log.is_completed():
        raise RuntimeError(
            f"Episode {episode_id} outcome exists but resume=False. "
            "Use --resume or delete the episode directory."
        )

    # Load fixture
    manifest = load_fixture(fixture_id, fixtures_root)
    evaluator = Evaluator(manifest, fixture_dir=fixtures_root / fixture_id)

    # Set up staging workspace (fresh copy of fixture workspace)
    fixture_workspace = fixtures_root / fixture_id / "workspace"
    staging_workspace = episode_dir / "workspace"
    allowlisted = manifest.get("editable_paths", [])
    workspace_cfg = WorkspaceConfig(
        workspace_root=staging_workspace,
        allowlisted_paths=allowlisted,
        condition=condition,
        task_root="/task",
        mypy_config=manifest.get("mypy_config"),
        test_command=manifest.get("test_command"),
        unsafe_local=unsafe_local,
        container_image=container_image,
    )

    # Compute initial baseline diagnostics (offline from saved fixture data)
    baseline_diag = _load_baseline_diagnostics(fixtures_root / fixture_id, manifest)
    saved_state = ep_log.load_state() if resume else None
    if saved_state:
        if not staging_workspace.exists():
            raise RuntimeError(f"Resume blocked for {episode_id}: workspace is missing")
        expected_hash = saved_state["workspace_hash"]
        actual_hash = hash_workspace(staging_workspace, allowlisted)
        resume_workspace_diverged = actual_hash != expected_hash
    else:
        resume_workspace_diverged = False
        if staging_workspace.exists():
            shutil.rmtree(staging_workspace)
        shutil.copytree(
            str(fixture_workspace), str(staging_workspace),
            ignore=shutil.ignore_patterns(
                ".pytest_cache", ".mypy_cache", "__pycache__", "*.pyc"
            ),
        )
    workspace_hash = hash_workspace(staging_workspace, allowlisted)

    # Build initial report
    if condition == "expanded":
        initial_report = render_expanded(baseline_diag) if baseline_diag else "[DIAGNOSTICS UNAVAILABLE]"
    else:
        initial_report = render_grouped(baseline_diag) if baseline_diag else "[DIAGNOSTICS UNAVAILABLE]"

    source_block = load_public_files(staging_workspace, manifest)
    initial_prompt = build_prompt(prompt_template, initial_report, source_block)

    if not saved_state:
        (episode_dir / "initial_prompt.txt").write_text(initial_prompt, encoding="utf-8")
        ep_log.append_event("episode_start", {
            "episode_id": episode_id,
            "fixture_id": fixture_id,
            "condition": condition,
            "workspace_hash": workspace_hash,
            "prompt_length": len(initial_prompt.encode("utf-8")),
            "report_hash": hashlib.sha256(initial_report.encode("utf-8")).hexdigest(),
            "source_hash": hashlib.sha256(source_block.encode("utf-8")).hexdigest(),
        })

    # Preflight: verify initial prompt bytes match (§11)
    outcome = (
        EpisodeOutcome.from_dict(saved_state["outcome"])
        if saved_state else
        EpisodeOutcome(episode_id=episode_id, fixture_id=fixture_id, condition=condition)
    )

    # Conversation history
    messages: list[dict[str, Any]] = (
        saved_state["messages"] if saved_state
        else [{"role": "user", "content": initial_prompt}]
    )
    if not saved_state:
        ep_log.save_messages(messages)
        ep_log.save_source_snapshot(0, staging_workspace)
        ep_log.save_state(outcome, messages, workspace_hash)
    else:
        ep_log.append_event("episode_resume", {"next_generation": outcome.generations_received + 1})

    generation_number = outcome.generations_received

    while generation_number < MAX_GENERATIONS:
        generation_number += 1
        logger.info("Episode %s generation %d", episode_id, generation_number)
        generation_path = episode_dir / f"gen_{generation_number:02d}.json"
        if (
            resume
            and not generation_path.exists()
            and ep_log.has_event("api_intent", generation_number)
        ):
            outcome.request_outcome_unknown = True
            outcome.termination_reason = "request_outcome_unknown"
            break

        # Log API intent before sending (§14)
        ep_log.append_event("api_intent", {
            "generation": generation_number,
            "message_count": len(messages),
        })

        if resume and generation_path.exists():
            gen = _generation_from_dict(
                json.loads(generation_path.read_text(encoding="utf-8"))
            )
            ep_log.append_event("response_replayed", {"generation": generation_number})
        else:
            if (
                isinstance(client, GroqExperimentClient)
                and not client.record_generation_slot(phase)
            ):
                outcome.budget_censored = True
                outcome.termination_reason = "generation_slot_cap"
                break
            gen = client.generate(
                episode_id=episode_id,
                generation_number=generation_number,
                messages=messages,
                tool_schema=TOOL_SCHEMA,
            )

        outcome.generations_received = generation_number

        # Persist raw response BEFORE executing any action (§14)
        ep_log.save_generation(gen)

        # Apply §8 decision table
        code = gen.termination_code
        if generation_number == 1:
            outcome.first_turn_evidence_status = (
                "truncated" if code == "token_censored" else "observed"
            )
            if code != "tool_call_received":
                outcome.first_turn_patch_submitted = False
                outcome.first_turn_patch_valid_envelope = False
                outcome.first_turn_patch_accepted = False
                outcome.first_turn_verified_progress = (
                    None if code == "token_censored" else False
                )

        if code == "model_identity_failure":
            ep_log.append_event("model_identity_failure", {"returned": gen.model_id_returned})
            outcome.model_identity_failure = True
            outcome.termination_reason = "model_identity_failure"
            break

        if code == "token_censored":
            ep_log.append_event("token_censored", {"generation": generation_number})
            outcome.token_censored = True
            outcome.termination_reason = "token_censored"
            break

        if code == "provider_content_filtered":
            ep_log.append_event("content_filtered", {"generation": generation_number})
            outcome.provider_content_filtered = True
            outcome.termination_reason = "provider_content_filtered"
            break

        if code == "model_action_invalid":
            ep_log.append_event("model_action_invalid", {"generation": generation_number, "detail": gen.error_detail})
            outcome.model_action_invalid = True
            outcome.termination_reason = "model_action_invalid"
            break

        if code == "technical_failure":
            ep_log.append_event("technical_failure", {"generation": generation_number, "detail": gen.error_detail})
            outcome.technical_failure = True
            outcome.termination_reason = "technical_failure"
            break

        if code == "request_outcome_unknown":
            ep_log.append_event("request_outcome_unknown", {"generation": generation_number})
            outcome.request_outcome_unknown = True
            outcome.termination_reason = "request_outcome_unknown"
            break

        if code == "quota_exhausted" or code == "http_attempt_cap":
            ep_log.append_event("budget_censored", {"generation": generation_number, "code": code})
            outcome.budget_censored = True
            outcome.termination_reason = code
            break

        if code == "model_final":
            # No tool call, non-empty final content
            ep_log.append_event("model_final", {"generation": generation_number})
            outcome.final_report_observed = True
            outcome.termination_reason = "model_final"
            break

        if code == "tool_call_received":
            tc = gen.tool_calls
            if len(tc) != 1:
                ep_log.append_event("model_action_invalid", {"reason": "multiple_tool_calls", "count": len(tc)})
                outcome.model_action_invalid = True
                outcome.termination_reason = "model_action_invalid"
                break

            tool_call = tc[0]
            raw_args = tool_call.get("function", {}).get("arguments", "")
            outcome.patches_submitted += 1

            if generation_number == 1:
                outcome.first_turn_patch_submitted = True
                outcome.first_turn_patch_valid_envelope = True

            # Parse arguments (no silent repair)
            parsed_patch = parse_tool_arguments(raw_args)

            if isinstance(parsed_patch, dict):
                ep_log.append_event("model_action_invalid", {
                    "generation": generation_number,
                    "reason": parsed_patch["error"],
                })
                if generation_number == 1:
                    outcome.first_turn_patch_valid_envelope = False
                    outcome.first_turn_patch_accepted = False
                    outcome.first_turn_verified_progress = False
                outcome.model_action_invalid = True
                outcome.termination_reason = "model_action_invalid"
                break

            # Apply patch + validate (§14: log intent before, result after)
            ep_log.append_event("tool_intent", {
                "generation": generation_number,
                "patch_hash": hashlib.sha256(parsed_patch.encode()).hexdigest()[:16],
                "pre_workspace_hash": hash_workspace(staging_workspace, allowlisted),
            })

            ep_log.save_patch(generation_number, parsed_patch)
            if mock:
                tool_result = _mock_tool_result(parsed_patch, generation_number, condition)
                patch_result = None
            else:
                current_hash = hash_workspace(staging_workspace, allowlisted)
                expected_post_hash = _predict_post_patch_hash(
                    episode_dir, generation_number, parsed_patch, allowlisted
                )
                if resume_workspace_diverged:
                    if current_hash != expected_post_hash:
                        raise RuntimeError(
                            f"Resume blocked for {episode_id}: state is neither the "
                            "recorded pre-patch nor predicted post-patch state"
                        )
                    parsed_diag, report, tests, infrastructure = validate_current_workspace(
                        workspace_cfg
                    )
                    applied_files = sorted(set(
                        match.group(1).split("\t")[0]
                        for match in re.finditer(r"^\+\+\+ b/(.+)$", parsed_patch, re.MULTILINE)
                    ))
                    patch_result_obj = PatchResult(
                        status="applied", reject_reason=None,
                        patch_hash=hashlib.sha256(parsed_patch.encode()).hexdigest(),
                        pre_state_hash=saved_state["workspace_hash"] if saved_state else "",
                        post_state_hash=current_hash,
                        applied_files=applied_files,
                        mypy_report=report,
                        runtime_summary=tests["summary"],
                        runtime_failures=tests.get("failures", []),
                        runtime_status=tests["status"],
                        parsed_diagnostics=parsed_diag,
                        output_limit_triggered=tests.get("output_limit", False),
                        infrastructure_failure=infrastructure,
                    )
                    resume_workspace_diverged = False
                    ep_log.append_event(
                        "patch_recovered_without_reapply",
                        {"generation": generation_number, "post_workspace_hash": current_hash},
                    )
                else:
                    patch_result_obj = patch_and_check(parsed_patch, workspace_cfg)
                tool_result = {
                    "status": patch_result_obj.status,
                    "feedback": format_feedback(patch_result_obj),
                    "post_workspace_hash": patch_result_obj.post_state_hash,
                    "policy_observations": patch_result_obj.policy_observations,
                    "infrastructure_failure": patch_result_obj.infrastructure_failure,
                }
                patch_result = patch_result_obj

            ep_log.append_event("tool_result", {
                "generation": generation_number,
                "status": tool_result["status"],
                "post_workspace_hash": tool_result.get("post_workspace_hash", "mock"),
            })
            ep_log.save_tool_result(generation_number, tool_result)
            if tool_result.get("infrastructure_failure"):
                outcome.technical_failure = True
                outcome.termination_reason = "technical_failure"
                break

            # Track first-turn acceptance
            accepted = tool_result["status"] == "applied"
            if generation_number == 1:
                outcome.first_turn_patch_accepted = accepted
                if not accepted:
                    outcome.first_turn_verified_progress = False
            if accepted:
                outcome.patches_accepted += 1

            # Evaluate snapshot after accepted patch
            if accepted:
                if mock:
                    snap = _mock_snapshot(episode_id, generation_number)
                else:
                    snap = evaluator.evaluate_snapshot(
                        snapshot_id=f"gen_{generation_number}_post_patch",
                        episode_id=episode_id,
                        generation_number=generation_number,
                        parsed_diag=patch_result.parsed_diagnostics if patch_result else None,
                        runtime_status=patch_result.runtime_status if patch_result else "unavailable",
                        workspace_root=staging_workspace,
                        baseline_parsed=baseline_diag,
                    )
                    snap.ambiguous_workaround = bool(
                        patch_result.policy_observations if patch_result else []
                    )
                    snap.compute_aggregates(evaluator.defect_ids)
                outcome.snapshots.append(snap)
                ep_log.save_snapshot(generation_number, snap)
                ep_log.save_source_snapshot(generation_number, staging_workspace)

                if generation_number == 1:
                    outcome.first_turn_verified_progress = snap.verified_progress_at_snapshot

            # Append assistant message and tool result to conversation
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tool_call["id"],
                        "type": "function",
                        "function": {
                            "name": "patch_and_check",
                            "arguments": raw_args,
                        },
                    }
                ],
            }
            if gen.assistant_content:
                assistant_msg["content"] = gen.assistant_content

            messages.append(assistant_msg)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": tool_result["feedback"],
            })
            ep_log.save_messages(messages)
            ep_log.save_state(
                outcome, messages, hash_workspace(staging_workspace, allowlisted)
            )

            # 4th generation tool call: executed but no 5th generation
            if generation_number == MAX_GENERATIONS:
                outcome.generation_cap_reached = True
                # Check if task succeeds despite cap
                if outcome.snapshots and outcome.snapshots[-1].task_checks_pass:
                    outcome.final_report_observed = False  # no 5th gen to report
                else:
                    outcome.budget_censored = True
                outcome.termination_reason = "generation_cap_reached"
                break

            continue  # loop for next generation

    # Compute aggregate outcomes
    outcome.compute_ever_verified_progress()
    outcome.compute_final_state()

    if not outcome.termination_reason or outcome.termination_reason == "pending":
        outcome.termination_reason = "completed"

    if outcome.generations_received >= 1 and outcome.first_turn_patch_submitted is None:
        outcome.first_turn_patch_submitted = False
        outcome.first_turn_patch_valid_envelope = False
        outcome.first_turn_patch_accepted = False
        if outcome.first_turn_evidence_status != "truncated":
            outcome.first_turn_verified_progress = False

    ep_log.append_event("episode_end", {"termination_reason": outcome.termination_reason})
    ep_log.save_state(
        outcome, messages, hash_workspace(staging_workspace, allowlisted)
    )
    ep_log.save_outcome(outcome)

    return outcome


def _load_baseline_diagnostics(
    fixture_dir: Path,
    manifest: dict[str, Any],
) -> ParsedDiagnostics | None:
    """Load pre-computed baseline diagnostics from fixture."""
    baseline_path = fixture_dir / "baseline_diagnostics.json"
    if not baseline_path.exists():
        return None
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    from experiment.diagnostics import DiagnosticRecord
    records = [DiagnosticRecord.from_dict(r) for r in data.get("records", [])]
    return ParsedDiagnostics(
        records=records,
        error_count=data.get("error_count", 0),
        raw_stdout=data.get("raw_stdout", ""),
        raw_stderr=data.get("raw_stderr", ""),
        exit_status=data.get("exit_status", 1),
        command=data.get("command", []),
        checker_version=data.get("checker_version", ""),
        parse_status=data.get("parse_status", "ok"),
        parse_error_detail=data.get("parse_error_detail", ""),
    )


def _mock_tool_result(
    patch_text: str, generation: int, condition: str,
) -> dict[str, Any]:
    """Return mock tool result for offline testing."""
    return {
        "status": "applied",
        "feedback": (
            "PATCH APPLIED: 1 file(s) modified.\n\n"
            "--- PUBLIC TEST RESULTS ---\n"
            "Status: PASS\n"
            "Exit code: 0\n\n"
            "--- TYPE-CHECKER REPORT ---\n"
            "=== TYPE-CHECKER REPORT ===\n"
            "--- 0 error(s), 0 record(s) total ---"
        ),
        "post_workspace_hash": f"mock_hash_gen_{generation}",
        "policy_observations": [],
        "infrastructure_failure": "",
    }


def _mock_snapshot(episode_id: str, generation: int) -> SnapshotState:
    """Return mock snapshot for offline testing."""
    snap = SnapshotState(
        snapshot_id=f"gen_{generation}_post_patch",
        episode_id=episode_id,
        generation_number=generation,
        mypy_status="pass",
        mypy_error_count=0,
        runtime_status="pass",
    )
    from experiment.evaluate import DefectCheckResult
    snap.defect_checks = [DefectCheckResult(defect_id="mock_defect", result="pass")]
    snap.introduced_error_status = "none_detected"
    snap.protected_files_intact = True
    snap.task_checks_pass = True
    snap.defects_repaired = 1
    snap.verified_progress_at_snapshot = True
    return snap


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------

def run_calibration(
    run_dir: Path,
    fixtures_root: Path,
    client: GroqExperimentClient | MockGroqClient,
    prompt_template: str,
    model_settings: dict[str, Any],
    budget: BudgetState,
    mock: bool = False,
    unsafe_local: bool = False,
    container_image: str | None = None,
) -> list[EpisodeOutcome]:
    """Run calibration phase (§10). At most 8 episodes."""
    cal_dir = run_dir / "calibration"
    cal_dir.mkdir(parents=True, exist_ok=True)

    # Use calibration fixtures if they exist, else use first 4 discovery fixtures
    cal_fixtures_root = fixtures_root / "calibration"
    if not cal_fixtures_root.exists():
        logger.warning("No calibration fixtures directory found. Using discovery fixtures.")
        cal_fixtures_root = fixtures_root / "discovery"

    outcomes: list[EpisodeOutcome] = []
    cal_fixture_ids = CALIBRATION_FIXTURE_IDS

    for i, fixture_id in enumerate(cal_fixture_ids[:CAL_MAX_EPISODES]):
        episode_id = f"cal_{i:02d}"
        ep_dir = cal_dir / episode_id

        # Check if fixture exists
        fixture_path = cal_fixtures_root / fixture_id
        if not fixture_path.exists():
            logger.warning("Calibration fixture %s not found, skipping.", fixture_id)
            continue

        for condition in ["expanded", "grouped"]:
            ep_id = f"{episode_id}_{condition}"
            ep_dir_c = ep_dir / condition
            outcome = run_episode(
                episode_id=ep_id,
                fixture_id=fixture_id,
                condition=condition,
                fixtures_root=cal_fixtures_root,
                episode_dir=ep_dir_c,
                client=client,
                prompt_template=prompt_template,
                model_settings=model_settings,
                budget=budget,
                mock=mock,
                unsafe_local=unsafe_local,
                resume=True,
                container_image=container_image,
                phase="calibration",
            )
            outcomes.append(outcome)
            logger.info("Calibration %s done: termination=%s", ep_id, outcome.termination_reason)

    return outcomes


def run_discovery(
    run_dir: Path,
    fixtures_root: Path,
    client: GroqExperimentClient | MockGroqClient,
    prompt_template: str,
    model_settings: dict[str, Any],
    budget: BudgetState,
    mock: bool = False,
    unsafe_local: bool = False,
    resume: bool = False,
    preflight: bool = False,
) -> list[EpisodeOutcome]:
    """Run discovery phase (§11). Exactly 16 episodes."""
    schedule_data = load_schedule(run_dir)
    episodes = schedule_data["episodes"]

    if len(episodes) != DISC_EPISODES:
        raise ValueError(f"Expected {DISC_EPISODES} episodes, got {len(episodes)}")

    disc_dir = run_dir / "discovery"
    disc_dir.mkdir(parents=True, exist_ok=True)
    disc_fixtures_root = fixtures_root / "discovery"

    if preflight:
        _run_preflight(run_dir, disc_fixtures_root, episodes)
        return []

    if not mock:
        # Enforce §16 discovery execution constraints
        freeze_path = run_dir / "frozen_config.json"
        if not freeze_path.exists():
            raise RuntimeError(
                "Discovery execution requires frozen_config.json. Run --freeze first."
            )
        if not (run_dir / "preflight.json").exists():
            raise RuntimeError(
                "Discovery execution requires a completed preflight.json."
            )
        frozen_data = json.loads(freeze_path.read_text(encoding="utf-8"))
        experiment_dir = Path(__file__).parent
        current_hash = _hash_directory(experiment_dir)
        if current_hash != frozen_data.get("code_hash"):
            raise RuntimeError(
                f"Stale code hash detected in discovery execution: "
                f"frozen={frozen_data.get('code_hash')} vs current={current_hash}."
            )
        current_fixtures_hash = _hash_directory(fixtures_root)
        if current_fixtures_hash != frozen_data.get("fixtures_hash"):
            raise RuntimeError("Stale fixture hash detected in discovery execution.")
        container_image = frozen_data.get("validation_image", {}).get("image_id")
        if not container_image:
            raise RuntimeError("Frozen validation image ID is missing.")
        inspected = _inspect_validation_image(container_image)
        if inspected["image_id"] != container_image:
            raise RuntimeError("Frozen validation image ID no longer resolves.")
    else:
        container_image = None

    outcomes: list[EpisodeOutcome] = []
    for ep_info in episodes:
        ep_id = ep_info["episode_id"]
        fixture_id = ep_info["fixture_id"]
        condition = ep_info["condition"]
        ep_dir = disc_dir / ep_id

        outcome = run_episode(
            episode_id=ep_id,
            fixture_id=fixture_id,
            condition=condition,
            fixtures_root=disc_fixtures_root,
            episode_dir=ep_dir,
            client=client,
            prompt_template=prompt_template,
            model_settings=model_settings,
            budget=budget,
            mock=mock,
            unsafe_local=unsafe_local,
            resume=resume,
            container_image=container_image,
            phase="discovery",
        )
        outcomes.append(outcome)
        logger.info(
            "Discovery episode %s done: termination=%s ever_progress=%s",
            ep_id, outcome.termination_reason, outcome.ever_verified_progress
        )

        # Save budget after each episode
        budget_path = run_dir / "budget_state.json"
        budget_path.write_text(json.dumps(budget.to_dict(), indent=2), encoding="utf-8")

    return outcomes


def _run_preflight(
    run_dir: Path,
    fixtures_root: Path,
    episodes: list[dict[str, Any]],
) -> None:
    """Local checks only — no API calls (§16)."""
    logger.info("Running preflight checks...")
    errors: list[str] = []

    for ep in episodes:
        fixture_id = ep["fixture_id"]
        fixture_dir = fixtures_root / fixture_id
        if not fixture_dir.exists():
            errors.append(f"Fixture not found: {fixture_id}")
        manifest_path = fixture_dir / "manifest.json"
        if not manifest_path.exists():
            errors.append(f"Manifest not found for: {fixture_id}")

    freeze_path = run_dir / "frozen_config.json"
    if not freeze_path.exists():
        errors.append("No frozen_config.json — run --freeze first.")

    if freeze_path.exists():
        try:
            frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
            if _hash_directory(Path(__file__).parent) != frozen.get("code_hash"):
                errors.append("Experiment artifacts differ from the operational freeze.")
            if _hash_directory(fixtures_root.parent) != frozen.get("fixtures_hash"):
                errors.append("Fixture artifacts differ from the operational freeze.")
            image_id = frozen.get("validation_image", {}).get("image_id")
            if not image_id:
                errors.append("Frozen validation image ID is missing.")
            else:
                inspected = _inspect_validation_image(image_id)
                if inspected["image_id"] != image_id:
                    errors.append("Frozen validation image identity mismatch.")
        except Exception as exc:
            errors.append(f"Frozen-environment check failed: {exc}")

    prompt_template = (Path(__file__).parent / "prompt.txt").read_text(encoding="utf-8")
    by_fixture: dict[str, list[dict[str, Any]]] = {}
    pair_checks: dict[str, Any] = {}
    for episode in episodes:
        by_fixture.setdefault(episode["fixture_id"], []).append(episode)
    for fixture_id, pair in by_fixture.items():
        conditions = {item["condition"] for item in pair}
        if len(pair) != 2 or conditions != {"expanded", "grouped"}:
            errors.append(f"Invalid condition pair for {fixture_id}.")
            continue
        try:
            manifest = load_fixture(fixture_id, fixtures_root)
            workspace = fixtures_root / fixture_id / "workspace"
            baseline = _load_baseline_diagnostics(fixtures_root / fixture_id, manifest)
            if baseline is None:
                raise RuntimeError("baseline diagnostics missing")
            source = load_public_files(workspace, manifest)
            pair_checks[fixture_id] = {
                "workspace_hash": _hash_directory(workspace),
                "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "scaffold_hash": hashlib.sha256(
                    prompt_template.replace("{report}", "{REPORT}").encode("utf-8")
                ).hexdigest(),
                "expanded_report_hash": hashlib.sha256(
                    render_expanded(baseline).encode("utf-8")
                ).hexdigest(),
                "grouped_report_hash": hashlib.sha256(
                    render_grouped(baseline).encode("utf-8")
                ).hexdigest(),
            }
        except Exception as exc:
            errors.append(f"Pair preflight failed for {fixture_id}: {exc}")

    if errors:
        for e in errors:
            logger.error("PREFLIGHT: %s", e)
        raise SystemExit(f"Preflight failed with {len(errors)} error(s).")

    logger.info(
        "Preflight passed. Budget estimate: %d episodes x 4 gens = %d generation slots max.",
        len(episodes), len(episodes) * 4
    )
    target = run_dir / "preflight.json"
    tmp = target.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
    tmp.write_text(json.dumps({
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "episodes": len(episodes),
        "pair_checks": pair_checks,
        "estimated_generation_slots": len(episodes) * MAX_GENERATIONS,
    }, indent=2), encoding="utf-8")
    _atomic_replace(tmp, target)


def _inspect_validation_image(image_ref: str) -> dict[str, Any]:
    """Resolve a mutable tag to Docker's immutable content-addressed image ID."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image_ref],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Docker is unavailable: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"Validation image {image_ref!r} is unavailable: {detail}"
        )
    records = json.loads(result.stdout)
    if len(records) != 1 or not records[0].get("Id", "").startswith("sha256:"):
        raise RuntimeError(f"Could not resolve immutable image ID for {image_ref!r}")
    image_id = records[0]["Id"]
    version = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            image_id, "python", "-c",
            "import sys,mypy.version,pytest;"
            "print(sys.version.split()[0]);print(mypy.version.__version__);print(pytest.__version__)",
        ],
        capture_output=True, text=True, timeout=30,
    )
    if version.returncode != 0:
        raise RuntimeError(
            f"Validation image version probe failed: {version.stderr.strip()}"
        )
    values = version.stdout.splitlines()
    if len(values) != 3:
        raise RuntimeError("Validation image returned an unexpected version payload")
    return {
        "reference": image_ref,
        "image_id": image_id,
        "repo_digests": records[0].get("RepoDigests", []),
        "python_version": values[0],
        "mypy_version": values[1],
        "pytest_version": values[2],
    }


# ---------------------------------------------------------------------------
# Freeze command (§10, §16)
# ---------------------------------------------------------------------------

def run_freeze(run_dir: Path, config: dict[str, Any]) -> None:
    """Save frozen_config.json (§10). Requires calibration review record and passing fixture validation."""
    review_path = run_dir / "calibration" / "review.json"
    if not review_path.exists():
        raise FileNotFoundError(
            "No calibration review record found. Complete calibration review before freezing."
        )

    val_path = run_dir / "fixture_validation.json"
    if not val_path.exists():
        raise FileNotFoundError(
            f"No fixture_validation.json found at {val_path}. "
            "Run fixture validation with --output before freezing."
        )
    val_data = json.loads(val_path.read_text(encoding="utf-8"))
    if not val_data.get("all_passed"):
        raise RuntimeError("Fixture validation did not pass. All fixtures must pass before freezing.")

    freeze_path = run_dir / "frozen_config.json"
    if freeze_path.exists():
        raise FileExistsError(
            f"frozen_config.json already exists at {freeze_path}. "
            "A new screen ID is required for any material change."
        )

    validation_image = _inspect_validation_image(config["validation_image"])
    experiment_dir = Path(__file__).parent
    code_hash = _hash_directory(experiment_dir)
    fixtures_hash = _hash_directory(Path("fixtures"))
    prompt_hash = hashlib.sha256(
        (experiment_dir / "prompt.txt").read_bytes()
    ).hexdigest()
    tool_schema_hash = hashlib.sha256(
        json.dumps(TOOL_SCHEMA, sort_keys=True).encode("utf-8")
    ).hexdigest()
    validation_hash = hashlib.sha256(val_path.read_bytes()).hexdigest()
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip() or None
        git_diff = subprocess.run(
            ["git", "diff", "--binary", "--no-ext-diff"],
            capture_output=True, timeout=10,
        ).stdout
        git_diff_hash = hashlib.sha256(git_diff).hexdigest()
    except (OSError, subprocess.SubprocessError):
        git_commit = None
        git_diff_hash = None

    frozen = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "code_hash": code_hash,
        "fixtures_hash": fixtures_hash,
        "prompt_hash": prompt_hash,
        "tool_schema_hash": tool_schema_hash,
        "fixture_validation_hash": validation_hash,
        "validation_image": validation_image,
        "package_versions": {
            "python": sys.version.split()[0],
            "groq": getattr(__import__("groq"), "__version__", "unknown"),
        },
        "code_provenance": {
            "git_commit": git_commit,
            "git_diff_hash": git_diff_hash,
            "content_hash": code_hash,
        },
        "screen_id": run_dir.name,
    }
    freeze_path.write_text(json.dumps(frozen, indent=2), encoding="utf-8")
    logger.info("Operational freeze saved to %s", freeze_path)


def _hash_directory(path: Path) -> str:
    h = hashlib.sha256()
    ignored = {".pytest_cache", ".mypy_cache", "__pycache__", ".git"}
    files: list[Path] = []
    for root, dirs, names in os.walk(path, onerror=lambda _exc: None):
        dirs[:] = sorted(d for d in dirs if d not in ignored)
        root_path = Path(root)
        files.extend(
            root_path / name for name in sorted(names) if not name.endswith(".pyc")
        )
    for f in sorted(files):
        h.update(f.relative_to(path).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Workload experiment runner.")
    parser.add_argument("--phase", choices=["calibration", "discovery"],
                        help="Phase to run")
    parser.add_argument("--run", required=True, help="Run directory")
    parser.add_argument("--execute", action="store_true",
                        help="Make real API calls (requires valid setup)")
    parser.add_argument("--mock", action="store_true",
                        help="Use mock client (no API calls)")
    parser.add_argument("--freeze", action="store_true",
                        help="Save operational freeze (requires calibration review)")
    parser.add_argument("--preflight", action="store_true",
                        help="Run local preflight checks only")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from saved state")
    parser.add_argument("--unsafe-local", action="store_true",
                        help="[DEV ONLY] Run mypy/tests locally without containers")
    parser.add_argument("--fixtures-root", default="fixtures",
                        help="Path to fixtures root directory")
    args = parser.parse_args()

    run_dir = Path(args.run)
    run_dir.mkdir(parents=True, exist_ok=True)

    fixtures_root = Path(args.fixtures_root)

    # Discovery requires --execute or --mock; enforce no unsafe-local
    if args.phase == "discovery" and args.execute and args.unsafe_local:
        parser.error("--unsafe-local is not allowed in discovery execution.")

    # Load config
    config_path = Path(__file__).parent / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    model_settings = {k: v for k, v in config.items() if k in CONFIG_KEYS_FOR_MODEL}
    model_id = config["model"]

    # Load prompt template
    prompt_path = Path(__file__).parent / "prompt.txt"
    prompt_template = prompt_path.read_text(encoding="utf-8")

    if args.freeze:
        run_freeze(run_dir, config)
        return

    if args.preflight:
        if args.phase == "discovery":
            disc_fixtures_root = fixtures_root / "discovery"
            schedule_data = load_schedule(run_dir)
            _run_preflight(run_dir, disc_fixtures_root, schedule_data["episodes"])
            return
        else:
            parser.error("--preflight requires --phase discovery.")

    # Build budget
    budget = BudgetState()
    budget_path = run_dir / "budget_state.json"
    if budget_path.exists() and args.resume:
        saved = json.loads(budget_path.read_text(encoding="utf-8"))
        for k, v in saved.items():
            if hasattr(budget, k):
                setattr(budget, k, v)
        logger.info("Resumed budget: %d tokens used, %d HTTP attempts",
                    budget.daily_tokens_used, budget.total_http_attempts)

    # Build client
    validation_image_id: str | None = None
    if args.mock:
        # Mock client: provide diverse scripted responses
        client: GroqExperimentClient | MockGroqClient = MockGroqClient(
            responses=[
                # A tool call response
                {
                    "finish_reason": "tool_calls",
                    "tool_calls": [{
                        "id": "call_mock_001",
                        "type": "function",
                        "function": {
                            "name": "patch_and_check",
                            "arguments": json.dumps({"patch":
                                "--- a/workspace/main.py\n"
                                "+++ b/workspace/main.py\n"
                                "@@ -1,1 +1,1 @@\n"
                                "-x: int = 'bad'\n"
                                "+x: int = 42\n"
                            })
                        }
                    }],
                    "input_tokens": 500,
                    "output_tokens": 100,
                },
                # Final model response
                {
                    "finish_reason": "stop",
                    "content": "I have fixed the type error. The variable x is now correctly typed as an integer.",
                    "input_tokens": 600,
                    "output_tokens": 30,
                },
            ] * 32  # enough for all 16 discovery episodes (2 per episode)
        )
    elif args.execute:
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            parser.error("GROQ_API_KEY not set. Load from .env or environment.")
        if not args.unsafe_local:
            validation_image_id = _inspect_validation_image(
                config["validation_image"]
            )["image_id"]

        log_dir = run_dir / "api_logs"
        log_dir.mkdir(exist_ok=True)

        client = GroqExperimentClient(
            api_key=api_key,
            model_id=model_id,
            model_settings=model_settings,
            budget=budget,
            log_dir=log_dir,
        )
    else:
        parser.error("Specify --execute or --mock.")

    unsafe_local = args.unsafe_local

    if args.phase == "calibration":
        outcomes = run_calibration(
            run_dir=run_dir,
            fixtures_root=fixtures_root,
            client=client,
            prompt_template=prompt_template,
            model_settings=model_settings,
            budget=budget,
            mock=args.mock,
            unsafe_local=unsafe_local,
            container_image=validation_image_id,
        )
        logger.info("Calibration complete: %d episodes.", len(outcomes))

    elif args.phase == "discovery":
        outcomes = run_discovery(
            run_dir=run_dir,
            fixtures_root=fixtures_root,
            client=client,
            prompt_template=prompt_template,
            model_settings=model_settings,
            budget=budget,
            mock=args.mock,
            unsafe_local=unsafe_local,
            resume=args.resume,
        )
        logger.info("Discovery complete: %d episodes.", len(outcomes))

    else:
        parser.error("Specify --phase calibration or --phase discovery.")


if __name__ == "__main__":
    main()
