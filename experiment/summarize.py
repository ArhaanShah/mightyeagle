"""
summarize.py — Offline summary generator (§15).

Operates entirely from saved evidence. Never makes API calls.
Includes all 16 scheduled rows even when outcomes are missing.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_EPISODES = 16


def load_schedule(run_dir: Path) -> list[dict[str, Any]]:
    schedule_path = run_dir / "schedule.json"
    if not schedule_path.exists():
        return []
    data = json.loads(schedule_path.read_text(encoding="utf-8"))
    return data.get("episodes", [])


def load_outcome(episode_dir: Path) -> dict[str, Any] | None:
    outcome_path = episode_dir / "outcome.json"
    if not outcome_path.exists():
        return None
    try:
        return json.loads(outcome_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_review_labels(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Load review_labels.jsonl keyed by episode_id."""
    labels: dict[str, dict[str, Any]] = {}
    label_path = run_dir / "review_labels.jsonl"
    if not label_path.exists():
        return labels
    for line in label_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            ep_id = obj.get("episode_id", "")
            labels[ep_id] = obj
        except Exception:
            pass
    return labels


def _val(v: Any) -> str:
    """Format value for CSV/display, using explicit unknowns for None."""
    if v is None:
        return "unknown"
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def build_csv(rows: list[dict[str, Any]]) -> str:
    """Build results.csv content."""
    fieldnames = [
        "episode_id", "fixture_id", "condition", "pair_index",
        "ever_verified_progress", "final_verified_progress",
        "final_defects_repaired", "max_defects_repaired",
        "successful_completion",
        "first_turn_patch_submitted", "first_turn_patch_accepted",
        "first_turn_verified_progress", "review_genuine_first_generation_attempt",
        "generation_cap_reached", "token_censored", "budget_censored",
        "model_action_invalid", "technical_failure", "request_outcome_unknown",
        "rate_limit_censored",
        "provider_content_filtered", "model_identity_failure",
        "final_report_observed", "termination_reason",
        "generations_received", "patches_submitted", "patches_accepted",
        "forbidden_attempt_observed", "forbidden_workaround_in_final_state",
        "review_repair_attempt", "review_transparent_stopping",
        "review_unsupported_completion_claim",
        "status",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: _val(row.get(f)) for f in fieldnames})
    return output.getvalue()


def build_summary_md(
    run_dir: Path,
    scheduled_episodes: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    review_labels: dict[str, dict[str, Any]],
) -> str:
    """Build summary.md content per §15."""
    lines: list[str] = []
    lines.append(f"# Experiment Summary: {run_dir.name}")
    lines.append(f"\nGenerated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    # Counts
    total_sched = len(scheduled_episodes)
    completed = sum(1 for r in rows if r.get("status") == "completed")
    pending = sum(1 for r in rows if r.get("status") == "pending")
    missing = sum(1 for r in rows if r.get("status") == "missing")

    lines.append("## Run Counts")
    lines.append(f"- Scheduled episodes: {total_sched}")
    lines.append(f"- Completed: {completed}")
    lines.append(f"- Pending: {pending}")
    lines.append(f"- Missing: {missing}")
    lines.append("")

    # Error/censoring summary
    model_invalid = sum(1 for r in rows if r.get("model_action_invalid") is True)
    tech_fail = sum(1 for r in rows if r.get("technical_failure") is True)
    token_cens = sum(1 for r in rows if r.get("token_censored") is True)
    budget_cens = sum(1 for r in rows if r.get("budget_censored") is True)
    rate_cens = sum(1 for r in rows if r.get("rate_limit_censored") is True)
    unknown_out = sum(1 for r in rows if r.get("request_outcome_unknown") is True)
    content_filt = sum(1 for r in rows if r.get("provider_content_filtered") is True)
    id_fail = sum(1 for r in rows if r.get("model_identity_failure") is True)

    lines.append("## Flags and Censoring")
    lines.append(f"- Model action invalid: {model_invalid}")
    lines.append(f"- Technical failures: {tech_fail}")
    lines.append(f"- Token censored: {token_cens}")
    lines.append(f"- Budget censored: {budget_cens}")
    lines.append(f"- Rate-limit censored: {rate_cens}")
    lines.append(f"- Request outcome unknown: {unknown_out}")
    lines.append(f"- Content filtered: {content_filt}")
    lines.append(f"- Model identity failures: {id_fail}")
    lines.append("")

    # Pair table
    lines.append("## Episode Pairs")
    lines.append("")
    lines.append(
        "| Pair | Fixture | Exp EverProgress | Grp EverProgress | "
        "Exp Final | Grp Final | Exp Complete | Grp Complete | "
        "Exp FirstTurn | Grp FirstTurn |"
    )
    lines.append("|" + "|".join(["---"] * 10) + "|")

    # Group rows by pair
    pairs: dict[int, dict[str, dict]] = {}
    for row in rows:
        pi = row.get("pair_index", -1)
        cond = row.get("condition", "")
        if pi not in pairs:
            pairs[pi] = {}
        pairs[pi][cond] = row

    for pi in sorted(pairs.keys()):
        pair = pairs[pi]
        exp = pair.get("expanded", {})
        grp = pair.get("grouped", {})
        fixture = exp.get("fixture_id") or grp.get("fixture_id") or "?"
        lines.append(
            f"| {pi} | {fixture} | "
            f"{_val(exp.get('ever_verified_progress'))} | "
            f"{_val(grp.get('ever_verified_progress'))} | "
            f"{_val(exp.get('final_verified_progress'))} | "
            f"{_val(grp.get('final_verified_progress'))} | "
            f"{_val(exp.get('successful_completion'))} | "
            f"{_val(grp.get('successful_completion'))} | "
            f"{_val(exp.get('review_genuine_first_generation_attempt'))} | "
            f"{_val(grp.get('review_genuine_first_generation_attempt'))} |"
        )
    lines.append("")

    # Pair-level win counts (only over pairs with known relevant outcomes)
    exp_wins = 0
    grp_wins = 0
    ties = 0
    pairs_with_known = 0
    for pi, pair in pairs.items():
        exp = pair.get("expanded", {})
        grp = pair.get("grouped", {})
        e_prog = exp.get("ever_verified_progress")
        g_prog = grp.get("ever_verified_progress")
        if e_prog is None or g_prog is None:
            continue
        pairs_with_known += 1
        if e_prog is True and g_prog is False:
            exp_wins += 1
        elif g_prog is True and e_prog is False:
            grp_wins += 1
        elif e_prog == g_prog:
            ties += 1

    lines.append("## Pair Comparison (ever_verified_progress)")
    lines.append(f"- Pairs with known outcomes: {pairs_with_known} of {len(pairs)}")
    lines.append(f"- Expanded wins: {exp_wins}")
    lines.append(f"- Grouped wins: {grp_wins}")
    lines.append(f"- Ties: {ties}")
    lines.append(
        f"- Unresolved pairs (affect possible totals): {len(pairs) - pairs_with_known}"
    )
    lines.append("")
    lines.append(
        "> **Note**: Do not compute p-values or emit go/no-go from 16 observations. "
        "See §15 of the implementation plan for interpretation guidance."
    )
    lines.append("")

    # Budget
    budget_path = run_dir / "budget_state.json"
    if budget_path.exists():
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
        lines.append("## Token Budget")
        lines.append(f"- Daily tokens used: {budget.get('daily_tokens_used', 'unknown')}")
        lines.append(f"- Daily token allowance: {budget.get('daily_token_allowance', 'unknown')}")
        lines.append(f"- Total HTTP attempts: {budget.get('total_http_attempts', 'unknown')}")
        lines.append(f"- Max HTTP attempts: {budget.get('max_http_attempts', 'unknown')}")
        lines.append("")

    # Review completeness
    lines.append("## Review Completeness")
    labeled = len([ep_id for ep_id in [r.get("episode_id") for r in rows] if ep_id in review_labels])
    lines.append(f"- Episodes with review labels: {labeled} of {total_sched}")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate offline experiment summary.")
    parser.add_argument("--run", required=True, help="Run directory path")
    args = parser.parse_args()

    run_dir = Path(args.run)
    if not run_dir.exists():
        print(f"Run directory not found: {run_dir}")
        raise SystemExit(1)

    scheduled_episodes = load_schedule(run_dir)
    review_labels = load_review_labels(run_dir)

    # Build row for each scheduled episode
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for ep_info in scheduled_episodes:
        ep_id = ep_info["episode_id"]
        fixture_id = ep_info["fixture_id"]
        condition = ep_info["condition"]
        pair_index = ep_info.get("pair_index", -1)

        if ep_id in seen_ids:
            print(f"WARNING: Duplicate episode ID {ep_id} in schedule.")
        seen_ids.add(ep_id)

        ep_dir = run_dir / "discovery" / ep_id
        outcome = load_outcome(ep_dir)
        label = review_labels.get(ep_id, {})

        row: dict[str, Any] = {
            "episode_id": ep_id,
            "fixture_id": fixture_id,
            "condition": condition,
            "pair_index": pair_index,
        }

        if outcome is None:
            row["status"] = "missing"
            # Use pending/unknown for all outcome fields
            for field in [
                "ever_verified_progress", "final_verified_progress",
                "final_defects_repaired", "max_defects_repaired",
                "successful_completion", "first_turn_patch_submitted",
                "first_turn_patch_accepted", "first_turn_verified_progress",
                "generation_cap_reached", "token_censored", "budget_censored",
                "model_action_invalid", "technical_failure", "request_outcome_unknown",
                "provider_content_filtered", "model_identity_failure",
                "final_report_observed", "termination_reason",
                "generations_received", "patches_submitted", "patches_accepted",
                "forbidden_attempt_observed", "forbidden_workaround_in_final_state",
            ]:
                row[field] = None
            row["termination_reason"] = "pending"
        else:
            row["status"] = "completed" if outcome.get("termination_reason") not in ("pending", None) else "pending"
            row.update(outcome)

        # Merge review labels
        row["review_genuine_first_generation_attempt"] = label.get("genuine_first_generation_attempt")
        row["review_repair_attempt"] = label.get("genuine_first_generation_attempt")
        row["review_transparent_stopping"] = label.get("transparent_incomplete_report")
        row["review_unsupported_completion_claim"] = label.get("unsupported_completion_claim")
        row["artifact_successful_completion"] = row.get("successful_completion")
        workaround = label.get("workaround_review")
        if workaround == "confirmed":
            row["successful_completion"] = False
            row["ever_verified_progress"] = False
            row["final_verified_progress"] = False
            row["forbidden_workaround_in_final_state"] = True
        elif workaround == "unresolved":
            row["successful_completion"] = None
            row["ever_verified_progress"] = None
            row["final_verified_progress"] = None

        rows.append(row)

    # Check for unexpected/duplicate episode IDs in discovery dir
    disc_dir = run_dir / "discovery"
    if disc_dir.exists():
        actual_ids = {d.name for d in disc_dir.iterdir() if d.is_dir()}
        scheduled_ids = {ep["episode_id"] for ep in scheduled_episodes}
        unexpected = actual_ids - scheduled_ids
        if unexpected:
            print(f"WARNING: Unexpected episode directories: {sorted(unexpected)}")

    # Write CSV
    csv_content = build_csv(rows)
    csv_path = run_dir / "results.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    print(f"Results written to {csv_path}")

    # Write summary.md
    summary_content = build_summary_md(run_dir, scheduled_episodes, rows, review_labels)
    summary_path = run_dir / "summary.md"
    summary_path.write_text(summary_content, encoding="utf-8")
    print(f"Summary written to {summary_path}")

    # Print brief to stdout
    completed = sum(1 for r in rows if r.get("status") == "completed")
    pending = sum(1 for r in rows if r.get("status") in ("pending", "missing"))
    print(f"\nTotal: {len(rows)} scheduled | {completed} completed | {pending} pending/missing")


if __name__ == "__main__":
    main()
