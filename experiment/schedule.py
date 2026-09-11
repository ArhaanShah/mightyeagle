"""
schedule.py — Episode schedule generation with deterministic PRNG seed (§11).

Shuffles fixture IDs, assigns expanded/grouped order for each pair,
and saves the schedule to schedule.json. Never overwrites an existing schedule.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path


FIXTURE_IDS = [
    "f1_shared",   # Family 1: return-type, shared defect
    "f1_dist",     # Family 1: return-type, distributed defects
    "f2_shared",   # Family 2: interface/protocol, shared
    "f2_dist",     # Family 2: interface/protocol, distributed
    "f3_shared",   # Family 3: optional-value, shared
    "f3_dist",     # Family 3: optional-value, distributed
    "f4_shared",   # Family 4: typed config access, shared
    "f4_dist",     # Family 4: typed config access, distributed
]

# Calibration fixtures use separate IDs (not in discovery schedule)
CALIBRATION_FIXTURE_IDS = [
    "cal_clean",     # Clean completion
    "cal_trivial",   # Trivial single repair
    "cal_dist",      # Distributed repair
    "cal_long_exp",  # Long expanded report
]


def generate_schedule(seed: int, fixture_ids: list[str] | None = None) -> list[dict]:
    """
    Generate the 16-episode discovery schedule (§11).
    Shuffle 8 fixture IDs, assign 4 expanded-first and 4 grouped-first pairs,
    randomly distributed across the 8 fixtures.

    Returns list of 16 episode dicts, each with:
      - episode_id: str
      - fixture_id: str
      - condition: "expanded" | "grouped"
      - pair_index: int (0-7)
      - pair_order: "first" | "second"
      - runs_first: str (condition that runs first in this pair)
    """
    if fixture_ids is None:
        fixture_ids = list(FIXTURE_IDS)

    rng = random.Random(seed)

    # Shuffle fixture IDs
    shuffled = list(fixture_ids)
    rng.shuffle(shuffled)

    # Assign 4 pairs expanded-first, 4 grouped-first
    first_conditions = ["expanded"] * 4 + ["grouped"] * 4
    rng.shuffle(first_conditions)

    episodes: list[dict] = []
    for pair_idx, (fixture_id, first_cond) in enumerate(zip(shuffled, first_conditions)):
        second_cond = "grouped" if first_cond == "expanded" else "expanded"
        for order, cond in enumerate([first_cond, second_cond]):
            ep_idx = pair_idx * 2 + order
            episodes.append({
                "episode_id": f"ep_{ep_idx:02d}",
                "fixture_id": fixture_id,
                "condition": cond,
                "pair_index": pair_idx,
                "pair_order": "first" if order == 0 else "second",
                "runs_first": first_cond,
            })

    return episodes


def save_schedule(run_dir: Path, seed: int, episodes: list[dict]) -> None:
    """Save schedule.json. Raises FileExistsError if it already exists."""
    schedule_path = run_dir / "schedule.json"
    if schedule_path.exists():
        raise FileExistsError(
            f"Schedule already exists at {schedule_path}. "
            "Delete it explicitly to regenerate (this would invalidate previous runs)."
        )
    schedule_path.write_text(
        json.dumps({"seed": seed, "episodes": episodes}, indent=2),
        encoding="utf-8",
    )


def load_schedule(run_dir: Path) -> dict:
    """Load saved schedule.json."""
    schedule_path = run_dir / "schedule.json"
    if not schedule_path.exists():
        raise FileNotFoundError(f"No schedule found at {schedule_path}")
    return json.loads(schedule_path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate experiment schedule.")
    parser.add_argument("--seed", type=int, default=20260911,
                        help="PRNG seed (default: 20260911 per §11)")
    parser.add_argument("--run", required=True, help="Run directory path")
    parser.add_argument("--show", action="store_true",
                        help="Print schedule without saving")
    args = parser.parse_args()

    run_dir = Path(args.run)
    run_dir.mkdir(parents=True, exist_ok=True)

    episodes = generate_schedule(args.seed)

    if args.show:
        for ep in episodes:
            print(f"{ep['episode_id']}: fixture={ep['fixture_id']} "
                  f"condition={ep['condition']} pair={ep['pair_index']} "
                  f"order={ep['pair_order']}")
        return

    save_schedule(run_dir, args.seed, episodes)
    print(f"Schedule saved to {run_dir / 'schedule.json'} with seed {args.seed}.")
    print(f"  {len(episodes)} episodes, "
          f"{sum(1 for e in episodes if e['condition']=='expanded')} expanded, "
          f"{sum(1 for e in episodes if e['condition']=='grouped')} grouped.")


if __name__ == "__main__":
    main()
