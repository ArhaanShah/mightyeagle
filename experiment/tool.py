"""
tool.py — exact structured edit implementation.

Applies ordered, exact source replacements to a workspace and runs validation.
Returns structured feedback without exposing hidden oracle results.

Key safety properties (§7):
- Only allowlisted existing regular source files may be edited.
- No traversal, absolute paths, symlinks, renames, creates, deletes.
- Patches are applied atomically: all-or-nothing.
- Exact context matching; no fuzzy application.
- Suppression/type-erasure policy detection via AST analysis.
- Returns deterministic fixed-format feedback.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
import threading
import uuid
from io import StringIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unidiff import PatchSet, UnidiffParseError  # type: ignore[import-untyped]

from experiment.diagnostics import (
    ParsedDiagnostics,
    PathNormalizer,
    parse_mypy_json_output,
    render_expanded,
    render_grouped,
)


# ---------------------------------------------------------------------------
# Tool schema (JSON Schema for the Groq function call)
# ---------------------------------------------------------------------------

TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "edit_and_check",
        "description": (
            "Apply exact source replacements and return validation feedback. "
            "Submit 1–16 edits; each path is relative to the task root and each "
            "old value must exactly match one unique substring shown in the prompt. "
            "Only existing, allowlisted source files may be edited. "
            "Returns: application status, public runtime-test results, and the "
            "complete mypy type-checker report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old": {"type": "string"},
                            "new": {"type": "string"},
                        },
                        "required": ["path", "old", "new"],
                        "additionalProperties": False,
                    },
                    "description": (
                        "Example: {\"edits\":[{\"path\":\"pkg/example.py\","
                        "\"old\":\"value: int | None\",\"new\":\"value: int\"}]}"
                    ),
                }
            },
            "required": ["edits"],
            "additionalProperties": False,
        },
    },
}


# ---------------------------------------------------------------------------
# Patch rejection reasons
# ---------------------------------------------------------------------------

REJECT_REASONS = {
    "invalid_json": "Patch argument is not valid JSON.",
    "missing_edits": "Edit argument missing required 'edits' field.",
    "extra_properties": "Edit argument contains extra properties.",
    "not_array": "The 'edits' field must be an array.",
    "wrong_edit_shape": "Each edit must contain exactly path, old, and new string fields.",
    "too_many_edits": "The edits array must contain between 1 and 16 items.",
    "empty_old": "The 'old' value must be nonempty.",
    "same_text": "The 'old' and 'new' values must differ.",
    "multiple_match": "The 'old' value must occur exactly once in the current file.",
    "zero_match": "The 'old' value does not occur in the current file.",
    "overlapping_edits": "Edits overlap in a staged file.",
    "protected_file": "The target file is protected and cannot be edited.",
    "missing_patch": "Patch argument missing required 'patch' field.",
    "not_string": "The legacy 'patch' field must be a string.",
    "parse_error": "The patch could not be parsed as a unified diff.",
    "no_hunks": "The patch contains no hunks.",
    "absolute_path": "The patch references an absolute path, which is not allowed.",
    "traversal": "The patch references a path outside the task root (directory traversal).",
    "nonexistent_file": "The patch references a file that does not exist in the workspace.",
    "not_allowlisted": "The patch references a file not in the allowlist.",
    "symlink": "The patch references a symbolic link, which is not allowed.",
    "not_regular_file": "The patch target is not a regular file.",
    "rename_copy": "Rename and copy operations are not supported.",
    "binary_change": "Binary file changes are not supported.",
    "mode_change": "File mode changes are not supported.",
    "file_creation": "File creation is not supported.",
    "file_deletion": "File deletion is not supported.",
    "hunk_mismatch": "A patch hunk did not match the current file content exactly.",
    "duplicate_file": "The patch contains duplicate file headers for the same target path.",
    "suppression_violation": "The patch introduces a mypy suppression (e.g., '# type: ignore') on a line that fixes a type error by hiding it.",
    "type_erasure_violation": "The patch introduces unrestricted 'Any' in a way that is a confirmed type erasure.",
    "invalid_path": "The patch path is not a normalized task-relative POSIX path.",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PatchResult:
    status: str                   # "applied" | "rejected"
    reject_reason: str | None     # key from REJECT_REASONS or None
    reject_detail: str = ""       # additional context
    patch_hash: str = ""          # sha256 of attempted patch text
    pre_state_hash: str = ""      # sha256 of workspace before patch
    post_state_hash: str = ""     # sha256 of workspace after patch (if applied)
    applied_files: list[str] = field(default_factory=list)
    mypy_report: str = ""         # rendered condition-specific report
    runtime_summary: str = ""     # public test summary
    runtime_failures: list[str] = field(default_factory=list)
    runtime_status: str = ""      # "pass" | "fail" | "error" | "output_limit"
    parsed_diagnostics: ParsedDiagnostics | None = None
    # Suppression/erasure observations (for review, not fed back)
    policy_observations: list[str] = field(default_factory=list)
    output_limit_triggered: bool = False
    infrastructure_failure: str = ""


@dataclass
class WorkspaceConfig:
    """Configuration for a single episode workspace."""
    workspace_root: Path           # staging area for this episode
    allowlisted_paths: list[str]   # task-relative paths that may be edited
    condition: str                 # "expanded" | "grouped"
    protected_paths: list[str] = field(default_factory=list)
    task_root: str = "/task"       # canonical prefix shown to model
    mypy_config: str | None = None # path to mypy config (task-relative)
    test_command: list[str] | None = None  # command to run public tests
    checker_version: str = ""
    output_limit_bytes: int = 65536  # 64 KiB hard cap on test output
    unsafe_local: bool = False     # only for tests; discovery executor rejects this
    container_image: str | None = None  # immutable image ID/digest for collection


# ---------------------------------------------------------------------------
# Workspace hashing
# ---------------------------------------------------------------------------

def hash_workspace(workspace_root: Path, allowlisted: list[str]) -> str:
    """SHA-256 over sorted (path, content) pairs of allowlisted files."""
    h = hashlib.sha256()
    for rel in sorted(allowlisted):
        full = workspace_root / rel
        if full.exists():
            h.update(rel.encode())
            h.update(b"\x00")
            h.update(full.read_bytes())
            h.update(b"\x00")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Unified diff application (no fuzzy matching)
# ---------------------------------------------------------------------------

def _normalize_path(raw: str) -> str:
    """Strip leading a/ or b/ prefix from diff paths."""
    if raw.startswith("a/") or raw.startswith("b/"):
        return raw[2:]
    return raw


def _parse_unified_diff(patch_text: str) -> list[dict[str, Any]]:
    """
    Parse a unified diff into a list of file-change dicts.
    Returns: [{old_path, new_path, hunks: [{old_start, old_lines, new_start, new_lines, lines}]}]
    Raises ValueError with REJECT_REASONS key on structural problems.
    """
    if not patch_text.strip():
        return []
    for line in patch_text.splitlines():
        if line.startswith(("Binary files ", "GIT binary patch")):
            raise ValueError("binary_change")
        if line.startswith(("old mode ", "new mode ", "new file mode ", "deleted file mode ")):
            raise ValueError("mode_change")
        if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            raise ValueError("rename_copy")
    raw_targets = [
        _normalize_path(line[4:].split("\t")[0].strip())
        for line in patch_text.splitlines()
        if line.startswith("+++ ") and line[4:].split("\t")[0].strip() != "/dev/null"
    ]
    if len(raw_targets) != len(set(raw_targets)):
        raise ValueError("duplicate_file")
    try:
        patch_set = PatchSet(StringIO(patch_text))
    except (UnidiffParseError, ValueError, TypeError) as exc:
        raise ValueError("parse_error") from exc

    files: list[dict[str, Any]] = []
    for patched_file in patch_set:
        old_path = patched_file.source_file
        new_path = patched_file.target_file
        if old_path == "/dev/null" or patched_file.is_added_file:
            raise ValueError("file_creation")
        if new_path == "/dev/null" or patched_file.is_removed_file:
            raise ValueError("file_deletion")
        old_norm = _normalize_path(old_path)
        new_norm = _normalize_path(new_path)
        if old_norm != new_norm:
            raise ValueError("rename_copy")
        hunks: list[dict[str, Any]] = []
        for hunk in patched_file:
            hunks.append({
                "old_start": hunk.source_start,
                "old_count": hunk.source_length,
                "new_start": hunk.target_start,
                "new_count": hunk.target_length,
                "lines": [line.line_type + line.value.rstrip("\n\r") for line in hunk],
            })
        files.append({
            "old_path": old_path,
            "new_path": new_path,
            "path": new_norm,
            "hunks": hunks,
        })
    return files


def _parse_begin_patch(patch_text: str) -> list[dict[str, Any]]:
    """Parse the conservative, existing-file-only Begin-Patch envelope."""
    lines = patch_text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip()), None)
    if start is None or lines[start].strip() != "*** Begin Patch":
        raise ValueError("parse_error")
    end_positions = [i for i, line in enumerate(lines) if line.strip() == "*** End Patch"]
    if len(end_positions) != 1 or end_positions[0] <= start:
        raise ValueError("parse_error")
    end = end_positions[0]
    if any(line.strip() for line in lines[end + 1:]):
        raise ValueError("parse_error")
    files: list[dict[str, Any]] = []
    i = start + 1
    while i < end:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if not line.startswith("*** Update File: "):
            if line.startswith(("*** Add File:", "*** Delete File:", "*** Move to:")):
                raise ValueError("file_creation" if "Add" in line else "file_deletion" if "Delete" in line else "rename_copy")
            raise ValueError("parse_error")
        path = _normalize_path(line[len("*** Update File: "):].strip())
        if not path:
            raise ValueError("invalid_path")
        i += 1
        hunks: list[dict[str, Any]] = []
        while i < end and not lines[i].startswith("*** Update File: "):
            if not lines[i].strip():
                i += 1
                continue
            if not lines[i].startswith("@@"):
                raise ValueError("parse_error")
            i += 1
            body: list[str] = []
            while i < end and not lines[i].startswith(("@@", "*** Update File: ")):
                item = lines[i]
                if item and item[0] in " +-":
                    body.append(item)
                else:
                    raise ValueError("parse_error")
                i += 1
            if not body or not any(item.startswith(("-", "+")) for item in body):
                raise ValueError("no_hunks")
            if not any(item.startswith((" ", "-")) for item in body):
                raise ValueError("hunk_mismatch")
            hunks.append({"old_start": 0, "old_count": sum(item[0] in " -" for item in body),
                          "new_start": 0, "new_count": sum(item[0] in " +" for item in body),
                          "lines": body, "begin_patch": True})
        if not hunks:
            raise ValueError("no_hunks")
        files.append({"old_path": path, "new_path": path, "path": path, "hunks": hunks})
    if not files:
        raise ValueError("no_hunks")
    return files


def _apply_hunk(
    file_lines: list[str], hunk: dict[str, Any], line_offset: int = 0
) -> list[str] | None:
    """
    Apply one hunk to file_lines (0-indexed list of lines without \\n).
    Returns new lines or None if context does not match exactly.
    """
    # Unified diff uses source_start=0 for insertions into an empty file. File
    # creation is rejected, but a zero-length insertion into an existing file is
    # still valid at index zero.
    old_start = (hunk["old_start"] - 1 if hunk["old_start"] else 0) + line_offset
    hunk_lines = hunk["lines"]

    # Extract context+deletion lines for matching
    context_and_del: list[str] = []
    for hl in hunk_lines:
        if hl.startswith(" "):
            context_and_del.append(hl[1:])
        elif hl.startswith("-"):
            context_and_del.append(hl[1:])

    old_count = hunk["old_count"]

    # Validate: exact context match
    if old_start < 0 or old_start + old_count > len(file_lines):
        return None
    actual = file_lines[old_start : old_start + old_count]
    if actual != context_and_del:
        return None

    # Build new file content
    new_lines: list[str] = []
    for hl in hunk_lines:
        if hl.startswith(" "):
            new_lines.append(hl[1:])
        elif hl.startswith("+"):
            new_lines.append(hl[1:])
        elif hl.startswith("-"):
            pass  # skip removed lines
    return file_lines[:old_start] + new_lines + file_lines[old_start + old_count:]


def apply_patch(
    workspace_root: Path,
    patch_text: str,
    allowlisted: list[str],
) -> dict[str, Any]:
    """
    Parse and apply a unified diff atomically.
    Returns {"status": "applied"|"rejected", "reason": ..., "detail": ..., "files": [...]}
    """
    # Parse
    try:
        is_begin = patch_text.lstrip().startswith("*** Begin Patch")
        file_changes = _parse_begin_patch(patch_text) if is_begin else _parse_unified_diff(patch_text)
    except ValueError as exc:
        reason = exc.args[0] if exc.args else "parse_error"
        return {"status": "rejected", "reason": reason, "detail": str(exc), "files": []}

    if not file_changes:
        return {"status": "rejected", "reason": "no_hunks", "detail": "", "files": []}

    # Check for duplicate file headers
    seen_paths: set[str] = set()
    for fc in file_changes:
        p = fc["path"]
        if p in seen_paths:
            return {
                "status": "rejected",
                "reason": "duplicate_file",
                "detail": f"Duplicate: {p}",
                "files": [],
            }
        seen_paths.add(p)

    # Validate all paths before touching anything
    for fc in file_changes:
        path_str = fc["path"]
        if (
            not path_str
            or "\\" in path_str
            or path_str.startswith("/")
            or any(part in ("", ".") for part in path_str.split("/"))
        ):
            return {
                "status": "rejected", "reason": "invalid_path",
                "detail": path_str, "files": [],
            }
        # Absolute path check
        if os.path.isabs(path_str):
            return {
                "status": "rejected",
                "reason": "absolute_path",
                "detail": path_str,
                "files": [],
            }
        # Traversal check
        raw_path = workspace_root / path_str
        full_path = raw_path.resolve()
        try:
            full_path.relative_to(workspace_root.resolve())
        except ValueError:
            return {
                "status": "rejected",
                "reason": "traversal",
                "detail": path_str,
                "files": [],
            }
        # Must exist
        if not full_path.exists():
            return {
                "status": "rejected",
                "reason": "nonexistent_file",
                "detail": path_str,
                "files": [],
            }
        # Must be allowlisted
        if path_str not in allowlisted:
            return {
                "status": "rejected",
                "reason": "not_allowlisted",
                "detail": path_str,
                "files": [],
            }
        # Must be regular file (not symlink)
        # Check the unresolved target and every component below the workspace;
        # checking only the resolved path misses a symlink at the leaf.
        cursor = workspace_root
        has_symlink = False
        for part in Path(path_str).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                has_symlink = True
                break
        if has_symlink:
            return {
                "status": "rejected",
                "reason": "symlink",
                "detail": path_str,
                "files": [],
            }
        if not full_path.is_file():
            return {
                "status": "rejected",
                "reason": "not_regular_file",
                "detail": path_str,
                "files": [],
            }

    # Dry-run: verify all hunks apply before modifying anything
    new_contents: dict[str, str] = {}
    for fc in file_changes:
        full_path = workspace_root / fc["path"]
        original = full_path.read_text(encoding="utf-8")
        lines = original.splitlines()
        if not fc["hunks"]:
            return {
                "status": "rejected", "reason": "no_hunks",
                "detail": fc["path"], "files": [],
            }
        line_offset = 0
        for hunk in fc["hunks"]:
            if hunk.get("begin_patch"):
                old = [item[1:] for item in hunk["lines"] if item[0] in " -"]
                new = [item[1:] for item in hunk["lines"] if item[0] in " +"]
                matches = [
                    idx for idx in range(len(lines) - len(old) + 1)
                    if lines[idx:idx + len(old)] == old
                ]
                if len(matches) != 1:
                    return {"status": "rejected", "reason": "hunk_mismatch",
                            "detail": f"Expected one exact match, found {len(matches)}", "files": []}
                idx = matches[0]
                result = lines[:idx] + new + lines[idx + len(old):]
            else:
                result = _apply_hunk(lines, hunk, line_offset)
            if result is None:
                return {
                    "status": "rejected",
                    "reason": "hunk_mismatch",
                    "detail": f"Hunk at line {hunk['old_start']} of {fc['path']} did not match.",
                    "files": [],
                }
            if result == lines:
                return {
                    "status": "rejected", "reason": "no_hunks",
                    "detail": "Patch contains no effective changes", "files": [],
                }
            lines = result
            line_offset += hunk["new_count"] - hunk["old_count"]
        new_contents[fc["path"]] = "\n".join(lines)
        # Preserve trailing newline if original had one
        if original.endswith("\n") and not new_contents[fc["path"]].endswith("\n"):
            new_contents[fc["path"]] += "\n"

    # Apply atomically
    applied: list[str] = []
    for rel_path, content in new_contents.items():
        full_path = workspace_root / rel_path
        full_path.write_text(content, encoding="utf-8")
        applied.append(rel_path)

    return {"status": "applied", "reason": None, "detail": "", "files": applied}


# ---------------------------------------------------------------------------
# Policy checks (suppression / type erasure)
# ---------------------------------------------------------------------------

def _check_suppression_in_patch(patch_text: str) -> list[str]:
    """
    Detect added lines that contain '# type: ignore' or 'noqa:' patterns.
    Returns list of observation strings (not automatic rejections — flagged for review).
    """
    observations: list[str] = []
    for line in patch_text.splitlines():
        if not line.startswith("+"):
            continue
        content = line[1:]
        if re.search(r"#\s*type:\s*ignore", content):
            observations.append(f"Added line with type:ignore: {content!r}")
        if re.search(r"#\s*noqa", content):
            observations.append(f"Added line with noqa: {content!r}")
    return observations


def _check_unrestricted_any_in_patch(patch_text: str) -> list[str]:
    """
    Use AST analysis to detect unrestricted Any in added content.
    Only flags actual annotation use of Any, not strings/comments.
    """
    observations: list[str] = []
    # Collect added lines per file from patch
    added_blocks: dict[str, list[str]] = {}
    current_path: str | None = None
    for line in patch_text.splitlines():
        if line.startswith("+++ "):
            current_path = _normalize_path(line[4:].split("\t")[0].strip())
            added_blocks.setdefault(current_path, [])
        elif line.startswith("+") and current_path:
            added_blocks.setdefault(current_path, []).append(line[1:])

    for path, added_lines in added_blocks.items():
        if not path.endswith(".py"):
            continue
        source = "\n".join(added_lines)
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "Any":
                # Check if it's in an annotation context — simplistic but reasonable
                observations.append(
                    f"Possible unrestricted Any usage in {path} (line {node.lineno} in added block) — review required"
                )
                break
    return observations


def _count_type_erasure_uses(source: str) -> int:
    """Count confirmed ``Any`` annotations/casts, excluding comments/strings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns:
                roots.append(node.returns)
            args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            roots.extend(arg.annotation for arg in args if arg.annotation)
            if node.args.vararg and node.args.vararg.annotation:
                roots.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation:
                roots.append(node.args.kwarg.annotation)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "cast"
            and node.args
        ):
            roots.append(node.args[0])
    return sum(
        isinstance(child, ast.Name) and child.id == "Any"
        for root in roots for child in ast.walk(root)
    )


# ---------------------------------------------------------------------------
# Validation runners (unsafe_local mode for tests)
# ---------------------------------------------------------------------------

def _run_mypy_local(
    workspace_root: Path,
    mypy_config: str | None,
    allowlisted: list[str],
    checker_version: str,
    path_normalizer: PathNormalizer,
) -> ParsedDiagnostics:
    """Run mypy locally (unsafe_local mode only)."""
    cmd = [
        sys.executable, "-m", "mypy",
        "--output", "json",
        "--no-error-summary",
        "--no-incremental",
    ]
    if mypy_config:
        cmd += ["--config-file", mypy_config]
    # Add all editable source files
    for rel in sorted(allowlisted):
        cmd.append(str(workspace_root / rel))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(workspace_root),
        )
    except subprocess.TimeoutExpired:
        return ParsedDiagnostics(
            records=[], error_count=0,
            raw_stdout="", raw_stderr="TIMEOUT",
            exit_status=-1, command=cmd,
            checker_version=checker_version,
            parse_status="checker_crash",
            parse_error_detail="mypy timed out",
        )

    return parse_mypy_json_output(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_status=result.returncode,
        command=cmd,
        checker_version=checker_version,
        path_normalizer=path_normalizer,
    )


def _run_tests_local(
    workspace_root: Path,
    test_command: list[str],
    output_limit_bytes: int,
) -> dict[str, Any]:
    """Run public tests locally (unsafe_local mode only)."""
    resolved_cmd = [sys.executable if c == "python" else c for c in test_command]
    try:
        result = subprocess.run(
            resolved_cmd,
            capture_output=True,
            timeout=120,
            cwd=str(workspace_root),
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "summary": "Tests timed out.",
            "failures": [],
            "output_limit": False,
        }

    combined = (result.stdout + result.stderr)
    limit_hit = len(combined) > output_limit_bytes
    if limit_hit:
        combined = combined[:output_limit_bytes]

    stdout_text = combined.decode("utf-8", errors="replace")
    passed = result.returncode == 0

    return {
        "status": "pass" if passed else "fail",
        "summary": f"Exit code: {result.returncode}",
        "failures": [] if passed else [stdout_text[-2000:]],
        "output_limit": limit_hit,
    }


def _docker_base_command(
    workspace_root: Path,
    image: str,
    container_name: str,
) -> list[str]:
    """Build the fixed, credential-free validation sandbox command."""
    return [
        "docker", "run", "--rm", "--name", container_name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--cpus", "1.0",
        "--memory", "512m",
        "--pids-limit", "128",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
        "--mount", f"type=bind,source={workspace_root.resolve()},target=/task,readonly",
        "--workdir", "/task",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        "--env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        image,
    ]


def _run_limited_process(
    command: list[str], timeout: int, output_limit_bytes: int,
    container_name: str | None = None,
) -> tuple[int, bytes, bytes, bool, bool]:
    """Run without retaining unbounded candidate output.

    Returns (exit_code, stdout, stderr, output_limited, timed_out).
    """
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total_seen = 0
    limited = False
    lock = threading.Lock()

    def drain(name: str, stream: Any) -> None:
        nonlocal total_seen, limited
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            with lock:
                remaining = max(0, output_limit_bytes - total_seen)
                buffers[name].extend(chunk[:remaining])
                total_seen += len(chunk)
                if total_seen > output_limit_bytes:
                    limited = True

    threads = [
        threading.Thread(target=drain, args=("stdout", proc.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", proc.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        exit_code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        exit_code = proc.wait()
        if container_name:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
            )
    for thread in threads:
        thread.join(timeout=5)
    return exit_code, bytes(buffers["stdout"]), bytes(buffers["stderr"]), limited, timed_out


def _run_mypy_container(
    workspace_root: Path,
    mypy_config: str | None,
    allowlisted: list[str],
    checker_version: str,
    path_normalizer: PathNormalizer,
    image: str,
    output_limit_bytes: int,
) -> tuple[ParsedDiagnostics, str]:
    name = f"workload-check-{uuid.uuid4().hex[:12]}"
    inner = [
        "python", "-m", "mypy", "--output", "json", "--no-error-summary",
        "--cache-dir", "/tmp/mypy-cache",
    ]
    if mypy_config:
        inner += ["--config-file", mypy_config]
    inner.extend(sorted(allowlisted))
    command = _docker_base_command(workspace_root, image, name) + inner
    try:
        code, stdout_b, stderr_b, limited, timed_out = _run_limited_process(
            command, 60, output_limit_bytes, name
        )
    except (OSError, subprocess.SubprocessError) as exc:
        parsed = ParsedDiagnostics(
            [], 0, "", str(exc), -1, command, checker_version,
            parse_status="checker_crash", parse_error_detail="container launch failed",
        )
        return parsed, f"container launch failed: {exc}"
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    infrastructure = ""
    if timed_out:
        infrastructure = "mypy container timed out"
        code = -1
    elif code in (125, 126, 127):
        infrastructure = f"mypy container failed to start (exit {code})"
    elif limited:
        infrastructure = "mypy output limit exceeded"
        code = -1
    parsed = parse_mypy_json_output(
        stdout, stderr, code, command, checker_version, path_normalizer
    )
    return parsed, infrastructure


def _run_tests_container(
    workspace_root: Path,
    test_command: list[str],
    image: str,
    output_limit_bytes: int,
) -> tuple[dict[str, Any], str]:
    name = f"workload-test-{uuid.uuid4().hex[:12]}"
    inner = ["python" if c == "python" else c for c in test_command]
    # Do not load repository-controlled pytest configuration/plugins.
    if len(inner) >= 3 and inner[0:3] == ["python", "-m", "pytest"]:
        inner[3:3] = ["-c", "/dev/null", "-p", "no:cacheprovider"]
    command = _docker_base_command(workspace_root, image, name) + inner
    try:
        code, stdout_b, stderr_b, limited, timed_out = _run_limited_process(
            command, 120, output_limit_bytes, name
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "error", "summary": "Container launch failed.",
            "failures": [], "output_limit": False,
        }, f"test container launch failed: {exc}"
    output = (stdout_b + stderr_b).decode("utf-8", errors="replace")
    if timed_out:
        return {
            "status": "error", "summary": "Tests timed out.",
            "failures": [output[-2000:]], "output_limit": limited,
        }, "test container timed out"
    if code in (125, 126, 127):
        return {
            "status": "error", "summary": f"Container failed to start (exit {code}).",
            "failures": [output[-2000:]], "output_limit": limited,
        }, f"test container failed to start (exit {code})"
    return {
        "status": "pass" if code == 0 else "fail",
        "summary": f"Exit code: {code}",
        "failures": [] if code == 0 else [output[-2000:]],
        "output_limit": limited,
    }, ""


def validate_current_workspace(
    config: WorkspaceConfig,
) -> tuple[ParsedDiagnostics, str, dict[str, Any], str]:
    """Validate the current candidate state without applying another patch."""
    path_norm = PathNormalizer(
        host_root=str(config.workspace_root).replace("\\", "/"),
        task_root=config.task_root,
    )
    mypy_infra = ""
    if config.unsafe_local:
        parsed = _run_mypy_local(
            config.workspace_root, config.mypy_config, config.allowlisted_paths,
            config.checker_version, path_norm,
        )
    else:
        if not config.container_image:
            raise RuntimeError("Container image ID/digest is required outside unsafe-local mode.")
        parsed, mypy_infra = _run_mypy_container(
            config.workspace_root, config.mypy_config,
            config.allowlisted_paths, config.checker_version, path_norm,
            config.container_image, config.output_limit_bytes,
        )
    if config.condition == "expanded":
        report = render_expanded(parsed)
    elif config.condition == "grouped":
        report = render_grouped(parsed)
    else:
        raise ValueError(f"Unknown condition: {config.condition!r}")

    test_infra = ""
    if config.test_command and config.unsafe_local:
        tests = _run_tests_local(
            config.workspace_root, config.test_command, config.output_limit_bytes
        )
    elif config.test_command:
        tests, test_infra = _run_tests_container(
            config.workspace_root, config.test_command, config.container_image or "",
            config.output_limit_bytes,
        )
    else:
        tests = {
            "status": "error", "summary": "No public test command configured.",
            "failures": [], "output_limit": False,
        }
    return parsed, report, tests, mypy_infra or test_infra


# ---------------------------------------------------------------------------
# Structured exact-edit application
# ---------------------------------------------------------------------------

def _count_unrestricted_any(text: str) -> int:
    """Count real ``Any`` expression uses, excluding comments and strings."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    return sum(isinstance(node, ast.Name) and node.id == "Any" for node in ast.walk(tree))


def _path_security_check(
    workspace_root: Path,
    rel: str,
    allowlisted: set[str],
    protected: set[str],
) -> str | None:
    if not isinstance(rel, str) or not rel or "\\" in rel:
        return "invalid_path"
    if os.path.isabs(rel) or rel.startswith("/"):
        return "absolute_path"
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "traversal" if ".." in parts else "invalid_path"
    if rel in protected:
        return "protected_file"
    if rel not in allowlisted:
        return "not_allowlisted"
    full = workspace_root / rel
    try:
        full.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return "traversal"
    cursor = workspace_root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return "symlink"
    if not full.exists():
        return "nonexistent_file"
    if not full.is_file():
        return "not_regular_file"
    return None


def apply_edits(
    workspace_root: Path,
    edits: list[dict[str, str]],
    allowlisted: list[str],
    protected: list[str] | None = None,
) -> dict[str, Any]:
    """Apply exact replacements atomically, returning a deterministic result."""
    if not isinstance(edits, list) or not 1 <= len(edits) <= 16:
        return {"status": "rejected", "reason": "too_many_edits", "files": []}
    allow = set(allowlisted)
    protected_set = set(protected or [])
    staged: dict[str, str] = {}
    introduced_ranges: dict[str, list[tuple[int, int]]] = {}
    seen_replacements: set[tuple[str, str]] = set()
    for edit in edits:
        if (
            not isinstance(edit, dict)
            or set(edit) != {"path", "old", "new"}
            or not all(isinstance(edit[k], str) for k in ("path", "old", "new"))
        ):
            return {"status": "rejected", "reason": "wrong_edit_shape", "files": []}
        path, old, new = edit["path"], edit["old"], edit["new"]
        if (path, old) in seen_replacements:
            return {"status": "rejected", "reason": "overlapping_edits", "detail": path, "files": []}
        seen_replacements.add((path, old))
        reason = _path_security_check(workspace_root, path, allow, protected_set)
        if reason:
            return {"status": "rejected", "reason": reason, "detail": path, "files": []}
        if not old:
            return {"status": "rejected", "reason": "empty_old", "detail": path, "files": []}
        if old == new:
            return {"status": "rejected", "reason": "same_text", "detail": path, "files": []}
        if path not in staged:
            staged[path] = (workspace_root / path).read_text(encoding="utf-8")
            introduced_ranges[path] = []
        content = staged[path]
        matches = [m.start() for m in re.finditer(re.escape(old), content)]
        if len(matches) != 1:
            return {
                "status": "rejected",
                "reason": "multiple_match" if len(matches) > 1 else "zero_match",
                "detail": f"{path}: found {len(matches)} occurrence(s)",
                "files": [],
            }
        start = matches[0]
        end = start + len(old)
        if any(start < other_end and other_start < end
               for other_start, other_end in introduced_ranges[path]):
            return {"status": "rejected", "reason": "overlapping_edits", "detail": path, "files": []}
        delta = len(new) - len(old)
        introduced_ranges[path] = [
            (a + delta, b + delta) if a >= end else (a, b)
            for a, b in introduced_ranges[path]
        ]
        introduced_ranges[path].append((start, start + len(new)))
        staged[path] = content[:start] + new + content[end:]
        if "# type: ignore" in new and "# type: ignore" not in old:
            return {
                "status": "rejected",
                "reason": "suppression_violation",
                "detail": path,
                "files": [],
            }
        if _count_unrestricted_any(staged[path]) > _count_unrestricted_any(content):
            return {
                "status": "rejected",
                "reason": "type_erasure_violation",
                "detail": path,
                "files": [],
            }

    originals = {p: (workspace_root / p).read_text(encoding="utf-8") for p in staged}
    try:
        # Validate all content before changing the live workspace.  Individual
        # replacements are written through sibling temporary files and rolled
        # back if a filesystem error occurs.
        temporary: dict[str, Path] = {}
        for rel, content in staged.items():
            target = workspace_root / rel
            tmp = target.with_name(f".{target.name}.edit_{uuid.uuid4().hex}")
            tmp.write_text(content, encoding="utf-8")
            temporary[rel] = tmp
        replaced: list[str] = []
        for rel, tmp in temporary.items():
            os.replace(tmp, workspace_root / rel)
            replaced.append(rel)
    except Exception:
        for rel in replaced:
            (workspace_root / rel).write_text(originals[rel], encoding="utf-8")
        for tmp in temporary.values():
            if tmp.exists():
                tmp.unlink()
        return {"status": "rejected", "reason": "atomic_write_failed", "files": []}
    return {"status": "applied", "reason": None, "files": sorted(staged)}


def parse_edit_arguments(raw_args: str) -> list[dict[str, str]] | dict[str, str]:
    """Strictly parse the structured model envelope without normalizing it."""
    try:
        obj = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        return {"error": f"invalid_json: {exc}"}
    if not isinstance(obj, dict):
        return {"error": "invalid_json: arguments must be a JSON object"}
    if set(obj) != {"edits"}:
        return {"error": "missing_edits" if "edits" not in obj else "extra_properties"}
    edits = obj["edits"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= 16:
        return {"error": "too_many_edits" if isinstance(edits, list) else "not_array"}
    for edit in edits:
        if (
            not isinstance(edit, dict)
            or set(edit) != {"path", "old", "new"}
            or not all(isinstance(edit[k], str) for k in ("path", "old", "new"))
        ):
            return {"error": "wrong_edit_shape"}
        if not edit["old"]:
            return {"error": "empty_old"}
        if edit["old"] == edit["new"]:
            return {"error": "same_text"}
    return edits


def edit_and_check(edits: list[dict[str, str]], config: WorkspaceConfig) -> PatchResult:
    """Apply structured edits and validate the resulting immutable snapshot."""
    if not config.unsafe_local and not config.container_image:
        raise RuntimeError("Container image ID/digest is required outside unsafe-local mode.")
    pre_hash = hash_workspace(config.workspace_root, config.allowlisted_paths)
    edit_hash = hashlib.sha256(
        json.dumps(edits, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    result = apply_edits(
        config.workspace_root, edits, config.allowlisted_paths,
        getattr(config, "protected_paths", None),
    )
    if result["status"] != "applied":
        return PatchResult(
            status="rejected", reject_reason=result["reason"],
            reject_detail=result.get("detail", ""), patch_hash=edit_hash,
            pre_state_hash=pre_hash,
        )
    parsed, report, tests, infrastructure = validate_current_workspace(config)
    return PatchResult(
        status="applied", reject_reason=None, patch_hash=edit_hash,
        pre_state_hash=pre_hash,
        post_state_hash=hash_workspace(config.workspace_root, config.allowlisted_paths),
        applied_files=result["files"], mypy_report=report,
        runtime_summary=tests["summary"], runtime_failures=tests.get("failures", []),
        runtime_status=tests["status"], parsed_diagnostics=parsed,
        output_limit_triggered=tests.get("output_limit", False),
        infrastructure_failure=infrastructure,
    )


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------

def patch_and_check(
    patch_text: str,
    config: WorkspaceConfig,
) -> PatchResult:
    """
    Core implementation of the patch_and_check tool.
    Does NOT accept raw model JSON; callers must extract the patch string.
    """
    if not config.unsafe_local and not config.container_image:
        raise RuntimeError("Container image ID/digest is required outside unsafe-local mode.")

    pre_hash = hash_workspace(config.workspace_root, config.allowlisted_paths)
    patch_hash = hashlib.sha256(patch_text.encode()).hexdigest()

    # Policy observations (not automatic rejections unless confirmed)
    policy_obs: list[str] = []
    policy_obs.extend(_check_suppression_in_patch(patch_text))
    policy_obs.extend(_check_unrestricted_any_in_patch(patch_text))

    # Check explicit suppression (hard reject for confirmed # type: ignore on error line)
    # Per §7: "Reject a confirmed protected-file or explicit suppression/type-erasure
    #  policy violation with a fixed ordinary explanation."
    # We flag for review but don't auto-reject (the plan says "flag ambiguous cases").
    # Hard rejection only for clear `# type: ignore` additions:
    suppress_lines = [l for l in patch_text.splitlines()
                      if l.startswith("+") and re.search(r"#\s*type:\s*ignore", l[1:])]
    if suppress_lines:
        return PatchResult(
            status="rejected",
            reject_reason="suppression_violation",
            reject_detail=f"Added {len(suppress_lines)} line(s) with type:ignore.",
            patch_hash=patch_hash,
            pre_state_hash=pre_hash,
            policy_observations=policy_obs,
        )

    original_contents = {
        rel: (config.workspace_root / rel).read_text(encoding="utf-8")
        for rel in config.allowlisted_paths
        if (config.workspace_root / rel).is_file()
    }

    # Apply patch
    result = apply_patch(
        config.workspace_root,
        patch_text,
        config.allowlisted_paths,
    )

    if result["status"] == "rejected":
        return PatchResult(
            status="rejected",
            reject_reason=result["reason"],
            reject_detail=result.get("detail", ""),
            patch_hash=patch_hash,
            pre_state_hash=pre_hash,
            policy_observations=policy_obs,
        )

    erasure_files: list[str] = []
    for rel in result["files"]:
        old_count = _count_type_erasure_uses(original_contents.get(rel, ""))
        new_count = _count_type_erasure_uses(
            (config.workspace_root / rel).read_text(encoding="utf-8")
        )
        if new_count > old_count:
            erasure_files.append(rel)
    if erasure_files:
        for rel, content in original_contents.items():
            (config.workspace_root / rel).write_text(content, encoding="utf-8")
        return PatchResult(
            status="rejected",
            reject_reason="type_erasure_violation",
            reject_detail=f"Introduced unrestricted Any in: {', '.join(erasure_files)}",
            patch_hash=patch_hash,
            pre_state_hash=pre_hash,
            policy_observations=policy_obs,
        )

    post_hash = hash_workspace(config.workspace_root, config.allowlisted_paths)

    parsed_diag, mypy_report, test_result, infrastructure = validate_current_workspace(config)

    output_limit = test_result.get("output_limit", False)

    return PatchResult(
        status="applied",
        reject_reason=None,
        patch_hash=patch_hash,
        pre_state_hash=pre_hash,
        post_state_hash=post_hash,
        applied_files=result["files"],
        mypy_report=mypy_report,
        runtime_summary=test_result["summary"],
        runtime_failures=test_result.get("failures", []),
        runtime_status=test_result["status"],
        parsed_diagnostics=parsed_diag,
        policy_observations=policy_obs,
        output_limit_triggered=output_limit,
        infrastructure_failure=infrastructure,
    )


# ---------------------------------------------------------------------------
# Format feedback for model consumption
# ---------------------------------------------------------------------------

OUTPUT_CAP_NOTICE = "[OUTPUT LIMIT REACHED — remaining test output omitted]"


def format_feedback(result: PatchResult) -> str:
    """
    Build the text returned to the model as the tool result.
    Does not include hidden oracle results or research metadata.
    """
    if result.status == "rejected":
        reason = result.reject_reason or "unknown"
        msg = REJECT_REASONS.get(reason, f"Edit rejected: {reason}")
        detail = f"\n\nDetail: {result.reject_detail}" if result.reject_detail else ""
        return f"EDITS REJECTED: {msg}{detail}"

    lines: list[str] = []
    lines.append(f"EDITS APPLIED: {len(result.applied_files)} file(s) modified.")
    lines.append("")

    # Runtime test result
    lines.append("--- PUBLIC TEST RESULTS ---")
    lines.append(f"Status: {result.runtime_status.upper()}")
    lines.append(result.runtime_summary)
    if result.runtime_failures:
        lines.append("Failures:")
        for f in result.runtime_failures:
            lines.append(textwrap.indent(f, "  "))
    if result.output_limit_triggered:
        lines.append(OUTPUT_CAP_NOTICE)
    lines.append("")

    # Mypy report
    lines.append("--- TYPE-CHECKER REPORT ---")
    lines.append(result.mypy_report)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parse model tool-call arguments
# ---------------------------------------------------------------------------

def parse_tool_arguments(raw_args: str) -> list[dict[str, str]] | dict[str, str]:
    """
    Parse the raw JSON arguments string from a model tool call.
    Returns exact edits on success, or a dict {"error": reason} on failure.
    No silent repair of malformed JSON.
    """
    return parse_edit_arguments(raw_args)
