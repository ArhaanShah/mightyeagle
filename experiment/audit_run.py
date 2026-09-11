"""Fact-only technical audit for a frozen screen_003 run."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from experiment.runner import _hash_directory
from experiment.review import validate_labels


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(run_dir: Path) -> dict[str, Any]:
    failures: list[str] = []
    facts: dict[str, Any] = {}
    schedule = _read(run_dir / "schedule.json").get("episodes", [])
    facts["scheduled_rows"] = len(schedule)
    if len(schedule) != 16 or len({row.get("episode_id") for row in schedule}) != 16:
        failures.append("schedule_incomplete_or_duplicate")
    blocks: dict[Any, list[dict[str, Any]]] = {}
    for row in schedule:
        blocks.setdefault(row.get("pair_index"), []).append(row)
    if len(blocks) != 8 or any(
        len(rows) != 2 or {row.get("condition") for row in rows} != {"expanded", "grouped"}
        for rows in blocks.values()
    ):
        failures.append("matched_blocks_invalid")

    outcomes: list[dict[str, Any]] = []
    generations: list[dict[str, Any]] = []
    for row in schedule:
        ep_dir = run_dir / "discovery" / row["episode_id"]
        outcome_path = ep_dir / "outcome.json"
        if not outcome_path.exists():
            failures.append(f"missing_outcome:{row['episode_id']}")
            continue
        outcome = _read(outcome_path)
        outcomes.append(outcome)
        snapshot_files = sorted(ep_dir.glob("gen_*_snapshot.json"))
        if outcome.get("snapshots", []) != [_read(path) for path in snapshot_files]:
            failures.append(f"raw_derived_snapshot_mismatch:{row['episode_id']}")
        generations.extend(_read(path) for path in sorted(ep_dir.glob("gen_[0-9][0-9].json")))
    facts["discovery_outcomes"] = len(outcomes)
    facts["discovery_generations"] = len(generations)
    if len(generations) > 48:
        failures.append("discovery_generation_budget_exceeded")

    calibration_generations = [
        _read(path) for path in sorted((run_dir / "calibration").glob("**/gen_[0-9][0-9].json"))
    ]
    facts["calibration_generations"] = len(calibration_generations)
    if len(calibration_generations) > 12:
        failures.append("calibration_generation_budget_exceeded")
    all_generations = calibration_generations + generations
    wrong_models = [
        g.get("episode_id") for g in all_generations
        if g.get("model_id_returned") != "openai/gpt-oss-120b"
    ]
    if wrong_models:
        failures.append(f"model_identity_mismatch:{wrong_models}")

    validation = _read(run_dir / "fixture_validation.json")
    for fixture, values in validation.get("fixtures", {}).items():
        if not values.get("passed"):
            failures.append(f"fixture_validation_failed:{fixture}")
        if values.get("expanded_bytes", 0) - values.get("grouped_bytes", 0) < 1000:
            failures.append(f"treatment_difference_too_small:{fixture}")
        if values.get("expanded_bytes", 0) < 1.5 * values.get("grouped_bytes", 1):
            failures.append(f"treatment_ratio_too_small:{fixture}")
        if max(values.get("group_sizes", [0])) < 8:
            failures.append(f"diagnostic_group_too_small:{fixture}")

    frozen = _read(run_dir / "frozen_config.json")
    if _hash_directory(Path("experiment")) != frozen.get("code_hash"):
        failures.append("frozen_code_hash_mismatch")
    if _hash_directory(Path("fixtures")) != frozen.get("fixtures_hash"):
        failures.append("frozen_fixture_hash_mismatch")
    frozen_sha = frozen.get("code_provenance", {}).get("git_commit")
    if not frozen_sha:
        failures.append("frozen_git_sha_missing")
    else:
        diff = subprocess.run(
            ["git", "diff", "--quiet", frozen_sha, "--", "experiment", "fixtures", "requirements-lock.txt"]
        )
        if diff.returncode != 0:
            failures.append("frozen_commit_not_reproducible")

    budget = _read(run_dir / "budget_state.json")
    facts["token_usage"] = budget.get("daily_tokens_used")
    facts["http_attempts"] = budget.get("total_http_attempts")
    facts["retry_attempts"] = budget.get("retry_http_attempts")
    if budget.get("total_http_attempts", 0) > 68 or budget.get("retry_http_attempts", 0) > 8:
        failures.append("http_budget_exceeded")
    observed_usage = sum(
        (g.get("total_input_tokens") or 0) + (g.get("total_output_tokens") or 0)
        for g in all_generations
    )
    facts["observed_generation_usage"] = observed_usage
    if observed_usage != budget.get("daily_tokens_used"):
        failures.append("quota_accounting_mismatch")

    review_errors = validate_labels(run_dir, {row["episode_id"] for row in schedule})
    failures.extend(f"review:{error}" for error in review_errors)
    facts["review_complete"] = not review_errors
    facts["pair_hash_checks_present"] = bool(_read(run_dir / "preflight.json").get("pair_checks"))
    if not facts["pair_hash_checks_present"]:
        failures.append("pair_hash_checks_missing")
    return {"valid": not failures, "failures": failures, "facts": facts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run)
    result = audit(run_dir)
    (run_dir / "audit.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
