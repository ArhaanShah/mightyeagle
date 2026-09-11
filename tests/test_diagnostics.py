"""
test_diagnostics.py — Tests for experiment/diagnostics.py (§16 acceptance gates).

Covers:
- Parser correctness on real mypy JSON output
- Notes attachment to parent errors
- Missing locations (no file, no line/col)
- Duplicate/repeated records
- Special characters (newlines, pipes, backslashes) in messages
- Expanded and grouped renderers
- Round-trip equality (expanded ↔ grouped decode → same canonical records)
- Parser against malformed output
- Canonical ordering
- PathNormalizer
"""
from __future__ import annotations

import json
import textwrap
from typing import Any

import pytest

from experiment.diagnostics import (
    DiagnosticRecord,
    ParsedDiagnostics,
    PathNormalizer,
    canonical_order,
    decode_expanded,
    decode_grouped,
    parse_mypy_json_output,
    render_expanded,
    render_grouped,
    verify_round_trip,
)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def make_record(
    file: str = "/task/pkg/mod.py",
    line: int | None = 10,
    col: int | None = 4,
    severity: str = "error",
    message: str = "Incompatible return value type",
    code: str | None = "return-value",
    occurrence_index: int = 0,
    notes: list[DiagnosticRecord] | None = None,
) -> DiagnosticRecord:
    return DiagnosticRecord(
        file=file,
        line=line,
        col=col,
        severity=severity,
        message=message,
        code=code,
        occurrence_index=occurrence_index,
        notes=notes or [],
    )


def make_parsed(records: list[DiagnosticRecord]) -> ParsedDiagnostics:
    error_count = sum(1 for r in records if r.severity == "error")
    return ParsedDiagnostics(
        records=records,
        error_count=error_count,
        raw_stdout="",
        raw_stderr="",
        exit_status=1 if error_count else 0,
        command=["mypy", "--output", "json", "pkg/"],
        checker_version="mypy 1.11.2",
    )


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestParser:
    def test_parse_single_error(self):
        stdout = json.dumps({
            "file": "pkg/mod.py",
            "line": 5,
            "col": 4,
            "severity": "error",
            "message": "Incompatible types in assignment",
            "code": "assignment",
        })
        result = parse_mypy_json_output(
            stdout=stdout,
            stderr="",
            exit_status=1,
            command=["mypy"],
            checker_version="mypy 1.11.2",
        )
        assert result.error_count == 1
        assert len(result.records) == 1
        assert result.records[0].severity == "error"
        assert result.records[0].code == "assignment"
        assert result.parse_status == "ok"

    def test_parse_multiple_errors_same_payload(self):
        """Multiple errors with same message (needed for groupability)."""
        lines = []
        for line_num in [5, 10, 15]:
            lines.append(json.dumps({
                "file": "pkg/mod.py",
                "line": line_num,
                "col": 4,
                "severity": "error",
                "message": "Incompatible return value type (got \"None\", expected \"float\")",
                "code": "return-value",
            }))
        stdout = "\n".join(lines)
        result = parse_mypy_json_output(
            stdout=stdout, stderr="", exit_status=1,
            command=["mypy"], checker_version="mypy 1.11.2",
        )
        assert result.error_count == 3
        # All three have the same non-location key
        keys = {r.non_location_key() for r in result.records}
        assert len(keys) == 1, "All records should have identical non-location payload for grouping"

    def test_parse_note_attached_to_error(self):
        """A note following an error should be attached as a child."""
        lines = [
            json.dumps({"file": "pkg/a.py", "line": 3, "col": 1,
                        "severity": "error", "message": "Arg mismatch", "code": "arg-type"}),
            json.dumps({"file": "pkg/a.py", "line": 3, "col": 1,
                        "severity": "note", "message": "Expected: int"}),
        ]
        stdout = "\n".join(lines)
        result = parse_mypy_json_output(
            stdout=stdout, stderr="", exit_status=1,
            command=["mypy"], checker_version="mypy 1.11.2",
        )
        assert result.error_count == 1
        assert len(result.records) == 1
        assert len(result.records[0].notes) == 1
        assert result.records[0].notes[0].severity == "note"

    def test_parse_missing_location(self):
        """Diagnostics without line/col must be preserved."""
        stdout = json.dumps({
            "file": "pkg/mod.py",
            "severity": "error",
            "message": "Missing return statement",
            "code": "return",
        })
        result = parse_mypy_json_output(
            stdout=stdout, stderr="", exit_status=1,
            command=["mypy"], checker_version="mypy 1.11.2",
        )
        assert result.records[0].line is None
        assert result.records[0].col is None

    def test_parse_unknown_fields_in_extras(self):
        """Unknown fields are stored in extras, not dropped."""
        stdout = json.dumps({
            "file": "pkg/mod.py",
            "line": 1,
            "col": 1,
            "severity": "error",
            "message": "Bad type",
            "code": "misc",
            "some_future_field": "some_value",
        })
        result = parse_mypy_json_output(
            stdout=stdout, stderr="", exit_status=1,
            command=["mypy"], checker_version="mypy 1.11.2",
        )
        assert result.records[0].extras.get("some_future_field") == "some_value"

    def test_parse_malformed_line_surfaced(self):
        """Malformed JSON lines are reported, not silently dropped."""
        stdout = "not json\n" + json.dumps({
            "file": "pkg/mod.py", "line": 1, "col": 1,
            "severity": "error", "message": "Bad", "code": "misc",
        })
        result = parse_mypy_json_output(
            stdout=stdout, stderr="", exit_status=1,
            command=["mypy"], checker_version="mypy 1.11.2",
        )
        assert result.parse_status == "parse_error"
        assert result.parse_error_detail  # non-empty

    def test_parse_empty_output_with_nonzero_exit_is_crash(self):
        """Empty output with exit code 2 indicates a crash, not clean."""
        result = parse_mypy_json_output(
            stdout="", stderr="Internal error", exit_status=2,
            command=["mypy"], checker_version="mypy 1.11.2",
        )
        assert result.parse_status == "checker_crash"

    def test_parse_clean_check(self):
        """Exit 0, empty output = clean check."""
        result = parse_mypy_json_output(
            stdout="", stderr="", exit_status=0,
            command=["mypy"], checker_version="mypy 1.11.2",
        )
        assert result.error_count == 0
        assert result.parse_status == "ok"

    def test_parse_occurrence_indices_assigned(self):
        """Occurrence indices are assigned stably."""
        lines = [
            json.dumps({"file": "a.py", "line": 1, "col": 1, "severity": "error",
                        "message": "E1", "code": "misc"}),
            json.dumps({"file": "a.py", "line": 2, "col": 1, "severity": "error",
                        "message": "E2", "code": "misc"}),
        ]
        result = parse_mypy_json_output(
            stdout="\n".join(lines), stderr="", exit_status=1,
            command=["mypy"], checker_version="mypy 1.11.2",
        )
        indices = [r.occurrence_index for r in result.records]
        assert indices == sorted(indices)
        assert len(set(indices)) == len(indices)

    def test_path_normalizer(self):
        norm = PathNormalizer("C:/Users/runner/workspace")
        assert norm.normalize("C:/Users/runner/workspace/pkg/mod.py") == "/task/pkg/mod.py"
        assert norm.normalize("C:/other/path.py") == "C:/other/path.py"

    def test_path_normalizer_backslash(self):
        norm = PathNormalizer("C:\\Users\\runner\\workspace")
        assert norm.normalize("C:\\Users\\runner\\workspace\\pkg\\mod.py") == "/task/pkg/mod.py"


# ---------------------------------------------------------------------------
# Renderer tests
# ---------------------------------------------------------------------------

class TestExpanded:
    def test_single_error_round_trip(self):
        rec = make_record()
        parsed = make_parsed([rec])
        verify_round_trip(parsed)

    def test_header_present(self):
        parsed = make_parsed([make_record()])
        text = render_expanded(parsed)
        assert text.splitlines()[0] == "=== TYPE-CHECKER REPORT ==="

    def test_error_count_in_footer(self):
        records = [make_record(occurrence_index=i, line=i) for i in range(3)]
        parsed = make_parsed(records)
        text = render_expanded(parsed)
        assert "3 error(s)" in text

    def test_special_chars_in_message(self):
        """Messages with newlines, pipes, backslashes must survive round-trip."""
        msg = 'Expected "int" but got "str | None"\nSee line 5\nPath: C:\\dir\\file.py'
        rec = make_record(message=msg)
        parsed = make_parsed([rec])
        decoded = decode_expanded(render_expanded(parsed))
        assert decoded[0].message == msg

    def test_missing_location_round_trip(self):
        rec = make_record(line=None, col=None)
        parsed = make_parsed([rec])
        decoded = decode_expanded(render_expanded(parsed))
        assert decoded[0].line is None
        assert decoded[0].col is None

    def test_notes_preserved(self):
        note = make_record(severity="note", message="Hint: check annotation", code=None, occurrence_index=1)
        rec = make_record(notes=[note])
        parsed = make_parsed([rec])
        decoded = decode_expanded(render_expanded(parsed))
        assert len(decoded[0].notes) == 1
        assert decoded[0].notes[0].message == "Hint: check annotation"

    def test_extras_preserved(self):
        rec = make_record()
        rec.extras = {"future_field": "value"}
        parsed = make_parsed([rec])
        decoded = decode_expanded(render_expanded(parsed))
        assert decoded[0].extras == {"future_field": "value"}


class TestGrouped:
    def test_grouped_header(self):
        parsed = make_parsed([make_record()])
        text = render_grouped(parsed)
        assert text.splitlines()[0] == "=== TYPE-CHECKER REPORT ==="

    def test_grouped_deduplicates_same_payload(self):
        """Same message at multiple locations should appear as one group."""
        records = [
            make_record(line=5, occurrence_index=0),
            make_record(line=10, occurrence_index=1),
            make_record(line=15, occurrence_index=2),
        ]
        parsed = make_parsed(records)
        text = render_grouped(parsed)
        # Should have only one payload block but three location lines
        payload_count = text.count("message:")
        assert payload_count == 1
        assert "multiplicity: 3" in text

    def test_different_payloads_separate_groups(self):
        records = [
            make_record(message="Error A", occurrence_index=0),
            make_record(message="Error B", occurrence_index=1),
        ]
        parsed = make_parsed(records)
        text = render_grouped(parsed)
        assert "Error A" in text
        assert "Error B" in text
        assert "multiplicity: 1" in text  # each group has 1

    def test_grouped_round_trip(self):
        records = [make_record(line=i, occurrence_index=i) for i in range(4)]
        parsed = make_parsed(records)
        verify_round_trip(parsed)

    def test_grouped_special_chars(self):
        msg = 'Got "None | str", expected "float"\nContext: C:\\path\\to\\file.py'
        records = [make_record(message=msg, line=i, occurrence_index=i) for i in range(2)]
        parsed = make_parsed(records)
        verify_round_trip(parsed)


class TestRoundTrip:
    def test_round_trip_mixed_payloads(self):
        records = [
            make_record(message="Msg A", line=1, occurrence_index=0),
            make_record(message="Msg A", line=2, occurrence_index=1),
            make_record(message="Msg B", code="arg-type", line=3, occurrence_index=2),
        ]
        parsed = make_parsed(records)
        verify_round_trip(parsed)

    def test_round_trip_with_notes(self):
        note = DiagnosticRecord(
            file="/task/pkg/a.py", line=3, col=1, severity="note",
            message="Consider this", code=None, occurrence_index=1,
        )
        rec = make_record(occurrence_index=0, notes=[note])
        parsed = make_parsed([rec])
        verify_round_trip(parsed)

    def test_round_trip_empty(self):
        parsed = make_parsed([])
        verify_round_trip(parsed)

    def test_round_trip_multiple_files(self):
        records = [
            make_record(file="/task/pkg/a.py", line=1, occurrence_index=0),
            make_record(file="/task/pkg/b.py", line=2, occurrence_index=1),
            make_record(file="/task/pkg/a.py", line=5, occurrence_index=2),
        ]
        parsed = make_parsed(records)
        verify_round_trip(parsed)

    def test_round_trip_none_code(self):
        rec = make_record(code=None)
        parsed = make_parsed([rec])
        verify_round_trip(parsed)

    def test_decode_expanded_invalid_header_raises(self):
        with pytest.raises(ValueError, match="header"):
            decode_expanded("not a report")

    def test_decode_grouped_invalid_header_raises(self):
        with pytest.raises(ValueError, match="header"):
            decode_grouped("not a report")


class TestCanonicalOrder:
    def test_sorted_by_file_then_line(self):
        records = [
            make_record(file="b.py", line=1, occurrence_index=0),
            make_record(file="a.py", line=5, occurrence_index=1),
            make_record(file="a.py", line=1, occurrence_index=2),
        ]
        ordered = canonical_order(records)
        assert ordered[0].file == "a.py" and ordered[0].line == 1
        assert ordered[1].file == "a.py" and ordered[1].line == 5
        assert ordered[2].file == "b.py"

    def test_missing_line_sorts_last(self):
        records = [
            make_record(line=None, occurrence_index=0),
            make_record(line=1, occurrence_index=1),
        ]
        ordered = canonical_order(records)
        assert ordered[0].line == 1
        assert ordered[1].line is None
