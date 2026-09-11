"""
test_patch_tool.py — Tests for experiment/tool.py (§16 acceptance gates).

Covers:
- Path traversal attempts rejected
- Symlink detection (mocked)
- Protected file edits rejected via allowlist
- Partially applicable patches rejected (all-or-none)
- False suppression positives: bare 'Any' in strings/comments not rejected
- Confirmed type:ignore additions rejected
- Valid patch applied correctly
- Duplicate file headers rejected
- Absolute paths rejected
- File creation / deletion rejected
- Rename/copy rejected
- Empty patch rejected
- parse_tool_arguments corner cases
- format_feedback output shape
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from experiment.tool import (
    REJECT_REASONS,
    PatchResult,
    WorkspaceConfig,
    apply_patch,
    format_feedback,
    parse_tool_arguments,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_workspace(files: dict[str, str]) -> Path:
    """Create a temporary workspace with the given files and return its path."""
    tmpdir = tempfile.mkdtemp()
    for rel, content in files.items():
        full = Path(tmpdir) / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return Path(tmpdir)


def minimal_diff(path: str, old_line: str, new_line: str) -> str:
    return (
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -1,1 +1,1 @@\n"
        f"-{old_line}\n"
        f"+{new_line}\n"
    )


# ---------------------------------------------------------------------------
# Valid patch application
# ---------------------------------------------------------------------------

class TestValidPatch:
    def test_simple_patch_applied(self):
        ws = make_workspace({"mod.py": "x: int = 1\n"})
        try:
            result = apply_patch(ws, minimal_diff("mod.py", "x: int = 1", "x: int = 42"), ["mod.py"])
            assert result["status"] == "applied"
            assert (ws / "mod.py").read_text() == "x: int = 42\n"
        finally:
            shutil.rmtree(ws)

    def test_multi_file_patch_applied(self):
        ws = make_workspace({
            "a.py": "a = 1\n",
            "b.py": "b = 2\n",
        })
        patch = (
            "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-a = 1\n+a = 10\n"
            "--- a/b.py\n+++ b/b.py\n@@ -1,1 +1,1 @@\n-b = 2\n+b = 20\n"
        )
        try:
            result = apply_patch(ws, patch, ["a.py", "b.py"])
            assert result["status"] == "applied"
            assert (ws / "a.py").read_text() == "a = 10\n"
            assert (ws / "b.py").read_text() == "b = 20\n"
        finally:
            shutil.rmtree(ws)

    def test_trailing_newline_preserved(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            result = apply_patch(ws, minimal_diff("mod.py", "x = 1", "x = 2"), ["mod.py"])
            assert result["status"] == "applied"
            content = (ws / "mod.py").read_text()
            assert content.endswith("\n")
        finally:
            shutil.rmtree(ws)


# ---------------------------------------------------------------------------
# Path security
# ---------------------------------------------------------------------------

class TestPathSecurity:
    def test_traversal_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            patch = (
                "--- a/../../../etc/passwd\n"
                "+++ b/../../../etc/passwd\n"
                "@@ -1,1 +1,1 @@\n-root\n+evil\n"
            )
            result = apply_patch(ws, patch, ["mod.py"])
            assert result["status"] == "rejected"
            assert result["reason"] == "traversal"
        finally:
            shutil.rmtree(ws)

    def test_absolute_path_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            patch = (
                "--- a//etc/passwd\n"
                "+++ b//etc/passwd\n"
                "@@ -1,1 +1,1 @@\n-x\n+y\n"
            )
            result = apply_patch(ws, patch, ["mod.py"])
            assert result["status"] == "rejected"
        finally:
            shutil.rmtree(ws)

    def test_not_allowlisted_rejected(self):
        ws = make_workspace({"src.py": "x = 1\n", "protected.py": "secret\n"})
        try:
            result = apply_patch(
                ws,
                minimal_diff("protected.py", "secret", "evil"),
                ["src.py"],  # protected.py not in allowlist
            )
            assert result["status"] == "rejected"
            assert result["reason"] == "not_allowlisted"
        finally:
            shutil.rmtree(ws)

    def test_nonexistent_file_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            result = apply_patch(
                ws,
                minimal_diff("ghost.py", "y = 2", "y = 3"),
                ["ghost.py", "mod.py"],
            )
            assert result["status"] == "rejected"
            assert result["reason"] == "nonexistent_file"
        finally:
            shutil.rmtree(ws)

    def test_symlink_rejected(self):
        """Symlinks must be rejected even if in allowlist."""
        ws = make_workspace({"real.py": "x = 1\n"})
        link = ws / "link.py"
        try:
            # Create symlink if supported
            try:
                link.symlink_to(ws / "real.py")
            except (OSError, NotImplementedError):
                pytest.skip("Symlinks not supported on this platform")
            result = apply_patch(
                ws,
                minimal_diff("link.py", "x = 1", "x = 2"),
                ["link.py"],
            )
            assert result["status"] == "rejected"
            assert result["reason"] == "symlink"
        finally:
            shutil.rmtree(ws)


# ---------------------------------------------------------------------------
# Diff structure violations
# ---------------------------------------------------------------------------

class TestDiffStructure:
    def test_no_hunks_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            result = apply_patch(ws, "", ["mod.py"])
            assert result["status"] == "rejected"
            assert result["reason"] in ("no_hunks", "parse_error")
        finally:
            shutil.rmtree(ws)

    def test_hunk_context_mismatch_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            patch = "--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,1 @@\n-wrong_line\n+new_line\n"
            result = apply_patch(ws, patch, ["mod.py"])
            assert result["status"] == "rejected"
            assert result["reason"] == "hunk_mismatch"
        finally:
            shutil.rmtree(ws)

    def test_file_creation_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            patch = "--- /dev/null\n+++ b/newfile.py\n@@ -0,0 +1,1 @@\n+y = 2\n"
            result = apply_patch(ws, patch, ["mod.py", "newfile.py"])
            assert result["status"] == "rejected"
            assert result["reason"] == "file_creation"
        finally:
            shutil.rmtree(ws)

    def test_file_deletion_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            patch = "--- a/mod.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n"
            result = apply_patch(ws, patch, ["mod.py"])
            assert result["status"] == "rejected"
            assert result["reason"] == "file_deletion"
        finally:
            shutil.rmtree(ws)

    def test_rename_rejected(self):
        ws = make_workspace({"old.py": "x = 1\n"})
        try:
            patch = "--- a/old.py\n+++ b/new.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
            result = apply_patch(ws, patch, ["old.py", "new.py"])
            assert result["status"] == "rejected"
            assert result["reason"] == "rename_copy"
        finally:
            shutil.rmtree(ws)

    def test_duplicate_file_header_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            patch = (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 3\n"
            )
            result = apply_patch(ws, patch, ["mod.py"])
            assert result["status"] == "rejected"
            assert result["reason"] == "duplicate_file"
        finally:
            shutil.rmtree(ws)

    def test_binary_change_rejected(self):
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            patch = "Binary files a/mod.py and b/mod.py differ\n"
            result = apply_patch(ws, patch, ["mod.py"])
            assert result["status"] == "rejected"
            assert result["reason"] == "binary_change"
        finally:
            shutil.rmtree(ws)


# ---------------------------------------------------------------------------
# Atomic application: all-or-nothing
# ---------------------------------------------------------------------------

class TestAtomic:
    def test_partial_patch_rejected_atomically(self):
        """If one file's hunk fails, nothing is applied."""
        ws = make_workspace({
            "a.py": "a = 1\n",
            "b.py": "b = 2\n",
        })
        try:
            # a.py patch is fine, b.py hunk has wrong context
            patch = (
                "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-a = 1\n+a = 10\n"
                "--- a/b.py\n+++ b/b.py\n@@ -1,1 +1,1 @@\n-WRONG_CONTEXT\n+b = 99\n"
            )
            result = apply_patch(ws, patch, ["a.py", "b.py"])
            assert result["status"] == "rejected"
            # a.py must remain unchanged
            assert (ws / "a.py").read_text() == "a = 1\n"
        finally:
            shutil.rmtree(ws)


# ---------------------------------------------------------------------------
# Suppression / type erasure checks
# ---------------------------------------------------------------------------

class TestSuppression:
    def test_type_ignore_addition_rejected(self):
        """Patch adding '# type: ignore' must be rejected."""
        from experiment.tool import patch_and_check, WorkspaceConfig
        ws = make_workspace({"mod.py": "x: int = 1\n"})
        try:
            cfg = WorkspaceConfig(
                workspace_root=ws,
                allowlisted_paths=["mod.py"],
                condition="expanded",
                unsafe_local=True,
            )
            patch = (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,1 @@\n"
                "-x: int = 1\n"
                "+x: int = 1  # type: ignore\n"
            )
            result = patch_and_check(patch, cfg)
            assert result.status == "rejected"
            assert result.reject_reason == "suppression_violation"
        finally:
            shutil.rmtree(ws)

    def test_any_in_string_not_rejected(self):
        """A bare 'Any' in a string literal is NOT a violation."""
        ws = make_workspace({"mod.py": 'msg = "hello"\n'})
        try:
            patch = (
                '--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,1 @@\n'
                '-msg = "hello"\n'
                '+msg = "Accept Any value here"\n'
            )
            result = apply_patch(ws, patch, ["mod.py"])
            # Should not be rejected for string-literal Any
            assert result["status"] == "applied"
        finally:
            shutil.rmtree(ws)

    def test_any_in_comment_not_rejected_at_patch_level(self):
        """'Any' in a comment doesn't cause automatic hard rejection."""
        ws = make_workspace({"mod.py": "x = 1\n"})
        try:
            patch = (
                "--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,1 @@\n"
                "-x = 1\n"
                "+x = 1  # Could be Any value\n"
            )
            result = apply_patch(ws, patch, ["mod.py"])
            assert result["status"] == "applied"
        finally:
            shutil.rmtree(ws)


# ---------------------------------------------------------------------------
# parse_tool_arguments
# ---------------------------------------------------------------------------

class TestParseToolArguments:
    def test_valid_args(self):
        args = json.dumps({"patch": "diff content"})
        result = parse_tool_arguments(args)
        assert result == "diff content"

    def test_invalid_json(self):
        result = parse_tool_arguments("not json")
        assert isinstance(result, dict)
        assert "error" in result
        assert "invalid_json" in result["error"]

    def test_missing_patch_key(self):
        result = parse_tool_arguments(json.dumps({"other": "value"}))
        assert isinstance(result, dict)
        assert "missing_patch" in result["error"]

    def test_extra_properties_rejected(self):
        result = parse_tool_arguments(json.dumps({"patch": "x", "extra": "y"}))
        assert isinstance(result, dict)
        assert "extra_properties" in result["error"]

    def test_patch_not_string(self):
        result = parse_tool_arguments(json.dumps({"patch": 42}))
        assert isinstance(result, dict)
        assert "not_string" in result["error"]

    def test_not_object(self):
        result = parse_tool_arguments(json.dumps([1, 2, 3]))
        assert isinstance(result, dict)
        assert "invalid_json" in result["error"]


# ---------------------------------------------------------------------------
# format_feedback
# ---------------------------------------------------------------------------

class TestFormatFeedback:
    def test_rejected_feedback_contains_reason(self):
        result = PatchResult(
            status="rejected",
            reject_reason="not_allowlisted",
            reject_detail="src.py",
        )
        feedback = format_feedback(result)
        assert "PATCH REJECTED" in feedback
        assert "allowlist" in feedback.lower()

    def test_applied_feedback_structure(self):
        result = PatchResult(
            status="applied",
            reject_reason=None,
            applied_files=["pkg/mod.py"],
            runtime_status="pass",
            runtime_summary="Exit code: 0",
            mypy_report="=== TYPE-CHECKER REPORT (EXPANDED) ===\n--- 0 error(s), 0 record(s) total ---",
        )
        feedback = format_feedback(result)
        assert "PATCH APPLIED" in feedback
        assert "PUBLIC TEST RESULTS" in feedback
        assert "TYPE-CHECKER REPORT" in feedback

    def test_output_limit_notice_in_feedback(self):
        result = PatchResult(
            status="applied",
            reject_reason=None,
            applied_files=["mod.py"],
            runtime_status="fail",
            runtime_summary="Exit code: 1",
            output_limit_triggered=True,
            mypy_report="=== TYPE-CHECKER REPORT (EXPANDED) ===\n--- 0 error(s), 0 record(s) total ---",
        )
        feedback = format_feedback(result)
        assert "OUTPUT LIMIT" in feedback
