"""
validate_fixtures.py — Validates all fixtures per §4.

Performs offline checks:
1. Baseline mypy fails, runtime tests pass, every defect check fails.
2. Reference repair passes mypy, runtime tests, all defect checks.
3. Baseline errors are accounted for by planted defects.
4. For distributed fixtures: each individual repair only fixes its intended defect.
5. Alternative legitimate repairs are not rejected.
6. Adversarial repairs: suppression, type erasure, required-symbol deletion, runtime regression.

Groupability check: at least one genuinely repeated non-location diagnostic payload.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from experiment.diagnostics import (
    ParsedDiagnostics,
    PathNormalizer,
    parse_mypy_json_output,
    render_expanded,
    render_grouped,
    verify_round_trip,
    canonical_order,
)


FIXTURES_ROOT = Path("fixtures")


@dataclass
class FixtureValidationResult:
    fixture_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    baseline_error_count: int = 0
    groupable: bool = False
    group_sizes: list[int] = field(default_factory=list)
    expanded_byte_length: int = 0
    grouped_byte_length: int = 0
    expanded_token_estimate: int = 0
    grouped_token_estimate: int = 0
    alternative_checked: bool = False

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


def run_mypy(
    workspace: Path,
    source_files: list[str],
    mypy_config: str | None = None,
    venv_python: str = sys.executable,
) -> ParsedDiagnostics:
    with tempfile.TemporaryDirectory(prefix="fixture-mypy-") as cache_dir:
        cmd = [
            venv_python, "-m", "mypy", "--output", "json",
            "--no-error-summary", "--no-incremental", "--cache-dir", cache_dir,
        ]
        if mypy_config:
            config_path = workspace / mypy_config
            if config_path.exists():
                cmd += ["--config-file", str(config_path)]
        for src in source_files:
            cmd.append(src)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(workspace),
        )

    # Get checker version
    ver_result = subprocess.run(
        [venv_python, "-m", "mypy", "--version"],
        capture_output=True, text=True
    )
    version = ver_result.stdout.strip()

    norm = PathNormalizer(str(workspace).replace("\\", "/"))
    return parse_mypy_json_output(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_status=result.returncode,
        command=cmd,
        checker_version=version,
        path_normalizer=norm,
    )


def run_tests(
    workspace: Path,
    test_command: list[str],
    venv_python: str = sys.executable,
) -> bool:
    """Returns True if tests pass."""
    cmd = [venv_python if c == "python" else c for c in test_command]
    if len(cmd) >= 3 and cmd[0:3] == [venv_python, "-m", "pytest"]:
        # ``-c os.devnull`` makes pytest treat ``NUL`` as a filesystem path on
        # Windows and can move rootdir to C:\. Disabling the cache plugin is
        # sufficient to keep fixture workspaces clean and permission-safe.
        cmd[3:3] = ["-p", "no:cacheprovider"]
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        cmd, capture_output=True, timeout=60, cwd=str(workspace), env=env
    )
    return result.returncode == 0


def run_oracle_checks(
    workspace: Path,
    fixture_dir: Path,
    defects: list[dict[str, Any]],
    venv_python: str = sys.executable,
) -> dict[str, str]:
    """Run defect oracle checks. Returns {defect_id: 'pass'|'fail'|'unavailable'}."""
    oracle_path = fixture_dir / "hidden_oracle" / "checks.py"
    if not oracle_path.exists():
        return {d["defect_id"]: "unavailable" for d in defects}

    results: dict[str, str] = {}
    for defect in defects:
        did = defect["defect_id"]
        # Run as subprocess to avoid contamination
        script = (
            f"import sys; sys.path.insert(0, r'{fixture_dir}'); "
            f"from hidden_oracle.checks import check_{did}; "
            f"from pathlib import Path; "
            f"print(check_{did}(Path(r'{workspace}')))"
        )
        result = subprocess.run(
            [venv_python, "-B", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout.strip()
        if output in ("pass", "fail"):
            results[did] = output
        else:
            results[did] = "unavailable"
    return results


def run_contract_checks(
    workspace: Path, fixture_dir: Path, contracts: list[dict[str, Any]]
) -> dict[str, str]:
    return run_oracle_checks(
        workspace, fixture_dir,
        [{"defect_id": item["oracle_id"]} for item in contracts],
    )


def required_symbols_present(
    workspace: Path, required: list[dict[str, Any]]
) -> bool:
    for item in required:
        try:
            tree = ast.parse((workspace / item["path"]).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return False
        name = item["name"]
        kind = item.get("kind", "function")
        if kind == "class":
            found = any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree))
        else:
            found = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name for node in ast.walk(tree))
        if not found:
            return False
    return True


def validate_fixture(
    fixture_id: str,
    fixtures_root: Path,
    show_diagnostics: bool = False,
) -> FixtureValidationResult:
    result = FixtureValidationResult(fixture_id=fixture_id)

    fixture_dir = fixtures_root / fixture_id
    if not fixture_dir.exists():
        result.errors.append(f"Fixture directory not found: {fixture_dir}")
        return result

    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.exists():
        result.errors.append("manifest.json not found")
        return result

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    workspace = fixture_dir / "workspace"
    reference = fixture_dir / "reference"
    defects = manifest.get("defects", [])
    contracts = manifest.get("contracts", [])
    required_symbols = manifest.get("required_symbols", [])
    editable = manifest.get("editable_paths", [])
    test_command = manifest.get("test_command", ["python", "-m", "pytest", "-q"])
    mypy_config = manifest.get("mypy_config")
    is_distributed = manifest.get("structure") == "distributed"

    banned = re.compile(
        r"\b(shared|distributed|easy|hard|D1|root_cause|expanded|grouped)\b",
        re.IGNORECASE,
    )
    for rel in manifest.get("public_paths", []):
        public_file = workspace / rel
        if public_file.is_file() and banned.search(public_file.read_text(encoding="utf-8")):
            result.errors.append(f"PUBLIC IDENTIFIER FAIL: banned experimental identifier in {rel}.")
    if is_distributed and len(editable) < 6:
        result.errors.append("STRUCTURE FAIL: distributed fixture needs at least six editable local decisions.")

    if not workspace.exists():
        result.errors.append("workspace/ not found")
        return result

    # --- Check 1: Baseline mypy fails, tests pass, defect checks fail ---
    baseline_diag = run_mypy(workspace, editable, mypy_config)
    result.baseline_error_count = baseline_diag.error_count

    if baseline_diag.error_count == 0:
        result.errors.append(
            f"CHECK 1 FAIL: Baseline mypy should fail but found 0 errors. "
            f"parse_status={baseline_diag.parse_status}"
        )
    else:
        result.info.append(f"CHECK 1: Baseline mypy fails with {baseline_diag.error_count} error(s). [PASS]")

    if baseline_diag.parse_status != "ok":
        result.errors.append(f"CHECK 1: Mypy parse error: {baseline_diag.parse_error_detail}")

    baseline_tests = run_tests(workspace, test_command)
    if not baseline_tests:
        result.errors.append("CHECK 1 FAIL: Baseline runtime tests should pass but they fail.")
    else:
        result.info.append("CHECK 1: Baseline runtime tests pass. [PASS]")

    baseline_oracle = run_oracle_checks(workspace, fixture_dir, defects)
    for did, res in baseline_oracle.items():
        if res == "pass":
            result.errors.append(f"CHECK 1 FAIL: Defect {did} oracle should fail at baseline but returned 'pass'.")
        elif res == "unavailable":
            result.errors.append(f"CHECK 1 FAIL: Defect {did} oracle unavailable at baseline.")
        else:
            result.info.append(f"CHECK 1: Defect {did} oracle fails at baseline. [PASS]")
    if not required_symbols_present(workspace, required_symbols):
        result.errors.append("CHECK 1 FAIL: required public symbols missing at baseline.")
    for contract_id, value in run_contract_checks(workspace, fixture_dir, contracts).items():
        if value != "pass":
            result.errors.append(f"CHECK 1 FAIL: runtime contract {contract_id} returned {value}.")

    # Groupability check (§4)
    from collections import Counter
    payload_counts = Counter(r.non_location_key() for r in baseline_diag.records if r.severity == "error")
    repeated = {k: v for k, v in payload_counts.items() if v > 1}
    result.groupable = len(repeated) > 0
    result.group_sizes = sorted(payload_counts.values(), reverse=True)

    if not result.groupable:
        result.errors.append(
            "GROUPABILITY FAIL: No repeated non-location diagnostic payloads. "
            "At least one required for grouped rendering to differ from expanded."
        )
    else:
        result.info.append(
            f"GROUPABILITY: {len(repeated)} repeated payload(s), sizes: {result.group_sizes}. [PASS]"
        )

    # Render both forms and verify round-trip
    try:
        verify_round_trip(baseline_diag)
        exp_text = render_expanded(baseline_diag)
        grp_text = render_grouped(baseline_diag)
        result.expanded_byte_length = len(exp_text.encode())
        result.grouped_byte_length = len(grp_text.encode())
        result.expanded_token_estimate = max(1, (len(exp_text) + 2) // 3)
        result.grouped_token_estimate = max(1, (len(grp_text) + 2) // 3)
        result.info.append(
            f"ROUND-TRIP: OK. Expanded={result.expanded_byte_length}B "
            f"Grouped={result.grouped_byte_length}B"
        )
        if result.baseline_error_count < 12 or result.baseline_error_count > 20:
            result.errors.append(
                f"THRESHOLD FAIL: expected 12–20 errors, got {result.baseline_error_count}."
            )
        if not result.group_sizes or result.group_sizes[0] < 8:
            result.errors.append(
                "THRESHOLD FAIL: largest exact diagnostic group must contain at least 8 occurrences."
            )
        if result.expanded_byte_length - result.grouped_byte_length < 1000:
            result.errors.append("THRESHOLD FAIL: expanded report is not 1000 bytes larger.")
        if (
            result.grouped_byte_length == 0
            or result.expanded_byte_length < 1.5 * result.grouped_byte_length
        ):
            result.errors.append("THRESHOLD FAIL: expanded report is below 1.5x grouped size.")
        if show_diagnostics:
            print(f"\n--- {fixture_id} EXPANDED ---")
            print(exp_text)
            print(f"\n--- {fixture_id} GROUPED ---")
            print(grp_text)
    except AssertionError as exc:
        result.errors.append(f"ROUND-TRIP FAIL: {exc}")

    # Save baseline diagnostics for runner use
    baseline_data = {
        "records": [r.to_dict() for r in baseline_diag.records],
        "error_count": baseline_diag.error_count,
        "raw_stdout": baseline_diag.raw_stdout,
        "raw_stderr": baseline_diag.raw_stderr,
        "exit_status": baseline_diag.exit_status,
        "command": baseline_diag.command,
        "checker_version": baseline_diag.checker_version,
        "parse_status": baseline_diag.parse_status,
        "parse_error_detail": baseline_diag.parse_error_detail,
    }
    (fixture_dir / "baseline_diagnostics.json").write_text(
        json.dumps(baseline_data, indent=2), encoding="utf-8"
    )

    # --- Check 2: Reference repair ---
    if not reference.exists():
        result.errors.append("Reference repair is mandatory.")
        result.warnings.append("reference/ not found — skipping checks 2–4.")
        return result

    # Apply reference patch to a temp copy
    with tempfile.TemporaryDirectory() as tmpdir:
        ref_workspace = Path(tmpdir) / "workspace"
        shutil.copytree(str(workspace), str(ref_workspace))
        # Apply reference files
        for rel in editable:
            ref_src = reference / rel
            if ref_src.exists():
                dst = ref_workspace / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(ref_src), str(dst))

        ref_diag = run_mypy(ref_workspace, editable, mypy_config)
        if ref_diag.error_count != 0:
            result.errors.append(
                f"CHECK 2 FAIL: Reference repair still has {ref_diag.error_count} mypy error(s)."
            )
        else:
            result.info.append("CHECK 2: Reference mypy passes. [PASS]")

        ref_tests = run_tests(ref_workspace, test_command)
        if not ref_tests:
            result.errors.append("CHECK 2 FAIL: Reference repair fails runtime tests.")
        else:
            result.info.append("CHECK 2: Reference runtime tests pass. [PASS]")

        ref_oracle = run_oracle_checks(ref_workspace, fixture_dir, defects)
        for did, res in ref_oracle.items():
            if res != "pass":
                result.errors.append(f"CHECK 2 FAIL: Defect {did} oracle should pass for reference but got '{res}'.")
            else:
                result.info.append(f"CHECK 2: Defect {did} oracle passes for reference. [PASS]")
        if not required_symbols_present(ref_workspace, required_symbols):
            result.errors.append("CHECK 2 FAIL: reference removed required public symbols.")
        if any(value != "pass" for value in run_contract_checks(ref_workspace, fixture_dir, contracts).values()):
            result.errors.append("CHECK 2 FAIL: reference violates a runtime contract.")

    # --- Check 4: For distributed fixtures, individual repairs ---
    if is_distributed and reference.exists():
        for defect_def in defects:
            did = defect_def["defect_id"]
            individual_files = defect_def.get("individual_repair_paths", editable)

            with tempfile.TemporaryDirectory() as tmpdir:
                ind_workspace = Path(tmpdir) / "workspace"
                shutil.copytree(str(workspace), str(ind_workspace))
                for rel in individual_files:
                    ref_src = reference / rel
                    if ref_src.exists():
                        dst = ind_workspace / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(ref_src), str(dst))

                ind_oracle = run_oracle_checks(ind_workspace, fixture_dir, defects)
                # Only the target defect should pass
                target_result = ind_oracle.get(did, "unavailable")
                if target_result == "unavailable":
                    result.warnings.append(
                        f"CHECK 4: Individual repair for {did} — oracle unavailable."
                    )
                elif target_result != "pass":
                    result.errors.append(
                        f"CHECK 4 FAIL: Applying individual repair for {did}, its oracle should pass but got '{target_result}'."
                    )
                else:
                    result.info.append(f"CHECK 4: Individual repair for {did} fixes only that defect. [PASS]")

                # Other defects should not now pass
                for other_id, other_result in ind_oracle.items():
                    if other_id == did:
                        continue
                    if other_result == "pass":
                        result.errors.append(
                            f"CHECK 4: Individual repair for {did} also fixed {other_id} — verify intended."
                        )

    # --- Check 6: Adversarial repairs ---
    # Inverse isolation: each baseline defect reintroduced into the fully
    # repaired tree must fail alone.
    if is_distributed:
        for defect_def in defects:
            did = defect_def["defect_id"]
            individual_files = defect_def.get("individual_repair_paths", editable)
            with tempfile.TemporaryDirectory() as tmpdir:
                inverse_workspace = Path(tmpdir) / "workspace"
                shutil.copytree(str(reference), str(inverse_workspace))
                for rel in individual_files:
                    source = workspace / rel
                    if source.exists():
                        target = inverse_workspace / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(source), str(target))
                inverse = run_oracle_checks(inverse_workspace, fixture_dir, defects)
                if inverse.get(did) != "fail":
                    result.errors.append(
                        f"CHECK 4 FAIL: Reintroduced {did} did not fail its oracle."
                    )
                for other_id, other_result in inverse.items():
                    if other_id != did and other_result != "pass":
                        result.errors.append(
                            f"CHECK 4 FAIL: Reintroducing {did} disturbed {other_id}."
                        )

    alternative = fixture_dir / "alternative"
    if alternative.exists():
        with tempfile.TemporaryDirectory() as tmpdir:
            alt_workspace = Path(tmpdir) / "workspace"
            shutil.copytree(str(workspace), str(alt_workspace))
            for rel in editable:
                source = alternative / rel
                if source.exists():
                    target = alt_workspace / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(source), str(target))
            alt_diag = run_mypy(alt_workspace, editable, mypy_config)
            alt_tests = run_tests(alt_workspace, test_command)
            alt_oracles = run_oracle_checks(alt_workspace, fixture_dir, defects)
            alt_contracts = run_contract_checks(alt_workspace, fixture_dir, contracts)
            if (
                alt_diag.parse_status != "ok"
                or alt_diag.error_count != 0
                or not alt_tests
                or any(value != "pass" for value in alt_oracles.values())
                or any(value != "pass" for value in alt_contracts.values())
                or not required_symbols_present(alt_workspace, required_symbols)
            ):
                result.errors.append("CHECK 5 FAIL: Alternative repair was rejected.")
            else:
                result.alternative_checked = True
                result.info.append("CHECK 5: Alternative legitimate repair passes. [PASS]")

    _check_adversarial(fixture_dir, workspace, editable, defects, test_command, mypy_config, result)

    return result


def _check_adversarial(
    fixture_dir: Path,
    workspace: Path,
    editable: list[str],
    defects: list[dict[str, Any]],
    test_command: list[str],
    mypy_config: str | None,
    result: FixtureValidationResult,
) -> None:
    """Check 6: Adversarial repairs must not earn verified progress."""
    adversarial_dir = fixture_dir / "adversarial"
    if not adversarial_dir.exists():
        result.warnings.append(
            "CHECK 6: No adversarial/ directory. "
            "Adversarial checks skipped (recommended: add suppression and erasure examples)."
        )
        return

    # Check each adversarial variant
    for adv_dir in sorted(adversarial_dir.iterdir()):
        if not adv_dir.is_dir():
            continue
        adv_name = adv_dir.name
        with tempfile.TemporaryDirectory() as tmpdir:
            adv_workspace = Path(tmpdir) / "workspace"
            shutil.copytree(str(workspace), str(adv_workspace))
            for rel in editable:
                adv_src = adv_dir / rel
                if adv_src.exists():
                    dst = adv_workspace / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(adv_src), str(dst))

            adv_oracle = run_oracle_checks(adv_workspace, fixture_dir, defects)
            adv_tests = run_tests(adv_workspace, test_command)
            all_defects_pass = all(v == "pass" for v in adv_oracle.values())

            if all_defects_pass and adv_tests:
                result.errors.append(
                    f"CHECK 6 FAIL: Adversarial variant '{adv_name}' passes all defect checks and tests — "
                    "this workaround should not earn verified progress."
                )
            else:
                result.info.append(
                    f"CHECK 6: Adversarial variant '{adv_name}' correctly fails. [PASS]"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate experiment fixtures.")
    parser.add_argument("--show-diagnostics", action="store_true",
                        help="Print rendered diagnostics for each fixture")
    parser.add_argument("--screen", choices=["initial", "final_rerun"],
                        default="initial", help="Select the offline screen fixture set")
    parser.add_argument("--fixture", help="Validate a specific fixture ID only")
    parser.add_argument("--fixtures-root", default="fixtures/discovery",
                        help="Path to fixtures root")
    parser.add_argument("--output", help="Optional path to save validation JSON results")
    args = parser.parse_args()

    fixtures_root = Path(args.fixtures_root)
    if not fixtures_root.exists():
        print(f"Fixtures root not found: {fixtures_root}")
        raise SystemExit(1)

    if args.fixture:
        fixture_ids = [args.fixture]
    elif args.screen == "final_rerun":
        fixture_ids = ["f1_shared", "f1_dist", "f2_shared", "f2_dist"]
    else:
        fixture_ids = sorted(d.name for d in fixtures_root.iterdir() if d.is_dir())

    if not fixture_ids:
        print("No fixtures found.")
        raise SystemExit(1)

    all_passed = True
    results_summary: dict[str, Any] = {}
    for fid in fixture_ids:
        print(f"\n{'=' * 60}")
        print(f"Validating: {fid}")
        print("=" * 60)
        result = validate_fixture(fid, fixtures_root, show_diagnostics=args.show_diagnostics)

        results_summary[fid] = {
            "passed": result.passed,
            "errors": result.errors,
            "warnings": result.warnings,
            "groupable": result.groupable,
            "group_sizes": result.group_sizes,
            "expanded_bytes": result.expanded_byte_length,
            "grouped_bytes": result.grouped_byte_length,
            "expanded_token_estimate": result.expanded_token_estimate,
            "grouped_token_estimate": result.grouped_token_estimate,
            "alternative_checked": result.alternative_checked,
        }

        for msg in result.info:
            print(f"  INFO: {msg}")
        for msg in result.warnings:
            print(f"  WARN: {msg}")
        for msg in result.errors:
            print(f"  ERROR: {msg}")

        if result.passed:
            print(f"  RESULT: PASSED [PASS]")
        else:
            print(f"  RESULT: FAILED [FAIL] ({len(result.errors)} error(s))")
            all_passed = False

    if "discovery" in fixtures_root.as_posix():
        for family in range(1, 5):
            family_ids = [
                fid for fid in results_summary if fid.startswith(f"f{family}_")
            ]
            if family_ids and not any(
                results_summary[fid].get("alternative_checked")
                for fid in family_ids
            ):
                print(
                    f"  ERROR: CHECK 5 FAIL: Family {family} has no "
                    "validated alternative repair."
                )
                all_passed = False

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_payload = {
            "all_passed": all_passed,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "fixtures": results_summary,
        }
        out_path.write_text(json.dumps(out_payload, indent=2), encoding="utf-8")
        print(f"\nSaved fixture validation results to {out_path}")

    print(f"\n{'=' * 60}")
    if all_passed:
        print("All fixtures passed validation.")
    else:
        print("VALIDATION FAILED. Fix errors before running the experiment.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
