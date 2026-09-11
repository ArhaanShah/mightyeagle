"""
diagnostics.py — Lossless mypy diagnostic parsing, canonical ordering, and
both rendering modes (expanded and grouped) as specified in §5.

Only the standard library is used here. No external dependencies.
"""
from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass, field, asdict
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Location:
    file: str
    line: int | None
    col: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "col": self.col}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Location":
        return Location(file=d["file"], line=d.get("line"), col=d.get("col"))


@dataclass
class DiagnosticRecord:
    """One diagnostic occurrence as emitted by mypy --output json."""
    file: str
    line: int | None
    col: int | None
    severity: str          # "error" | "note" | ...
    message: str
    code: str | None       # mypy error code, e.g. "return-value"
    # notes attached to this record (child notes that follow an error)
    notes: list["DiagnosticRecord"] = field(default_factory=list)
    # unknown extra fields preserved verbatim
    extras: dict[str, Any] = field(default_factory=dict)
    # stable occurrence index within the full list (set after parsing)
    occurrence_index: int = 0

    def non_location_key(self) -> tuple[str, str | None, str, str, str]:
        """Lossless grouping key for every field except this record's location/index.

        Notes are kept in the key in full.  This is deliberately conservative:
        notes at different locations are not folded together because doing so would
        make it impossible to reconstruct the original occurrence association.
        """
        note_sig = json.dumps(
            [n.to_dict() for n in self.notes], sort_keys=True, ensure_ascii=False
        )
        extras_sig = json.dumps(self.extras, sort_keys=True, ensure_ascii=False)
        return (self.severity, self.code, self.message, note_sig, extras_sig)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "occurrence_index": self.occurrence_index,
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "severity": self.severity,
            "message": self.message,
            "code": self.code,
            "notes": [n.to_dict() for n in self.notes],
        }
        if self.extras:
            d["extras"] = self.extras
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DiagnosticRecord":
        known = {"occurrence_index", "file", "line", "col", "severity", "message",
                 "code", "notes", "extras"}
        extras = dict(d.get("extras", {}))
        extras.update({k: v for k, v in d.items() if k not in known})
        notes = [DiagnosticRecord.from_dict(n) for n in d.get("notes", [])]
        return DiagnosticRecord(
            file=d.get("file", ""),
            line=d.get("line"),
            col=d.get("col"),
            severity=d.get("severity", ""),
            message=d.get("message", ""),
            code=d.get("code"),
            notes=notes,
            extras=extras,
            occurrence_index=d.get("occurrence_index", 0),
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DiagnosticRecord):
            return NotImplemented
        return (
            self.file == other.file
            and self.line == other.line
            and self.col == other.col
            and self.severity == other.severity
            and self.message == other.message
            and self.code == other.code
            and self.notes == other.notes
            and self.extras == other.extras
            and self.occurrence_index == other.occurrence_index
        )


@dataclass
class ParsedDiagnostics:
    """Full parsed result from one mypy invocation."""
    records: list[DiagnosticRecord]
    error_count: int          # count of severity=="error"
    raw_stdout: str
    raw_stderr: str
    exit_status: int
    command: list[str]
    checker_version: str      # from mypy --version or stderr

    # Parse outcome: "ok" | "parse_error" | "empty_after_error" | "checker_crash"
    parse_status: str = "ok"
    parse_error_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [r.to_dict() for r in self.records],
            "error_count": self.error_count,
            "exit_status": self.exit_status,
            "command": self.command,
            "checker_version": self.checker_version,
            "parse_status": self.parse_status,
            "parse_error_detail": self.parse_error_detail,
            # raw bytes not stored in to_dict to keep things manageable;
            # callers should persist raw_stdout/raw_stderr separately.
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Mypy --output json emits one JSON object per line (JSONL), NOT an array.
# Each object has: file, line, col, severity, message, code (optional)
# Notes follow errors and share the same line in output.

def parse_mypy_json_output(
    stdout: str,
    stderr: str,
    exit_status: int,
    command: list[str],
    checker_version: str,
    path_normalizer: "PathNormalizer | None" = None,
) -> ParsedDiagnostics:
    """
    Parse mypy --output json stdout into canonical DiagnosticRecord list.
    Assigns occurrence_index and attaches trailing notes to their parent error.
    Never silently drops records; parse errors are surfaced via parse_status.
    """
    raw_records: list[dict[str, Any]] = []
    parse_errors: list[str] = []

    for lineno, line in enumerate(stdout.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            parse_errors.append(f"line {lineno}: {exc}")
            continue
        if not isinstance(obj, dict):
            parse_errors.append(f"line {lineno}: not a JSON object")
            continue
        raw_records.append(obj)

    # Attach notes to their nearest preceding error
    records: list[DiagnosticRecord] = []
    for idx, obj in enumerate(raw_records):
        known_fields = {
            "file", "line", "col", "column", "severity", "message", "code"
        }
        extras = {k: v for k, v in obj.items() if k not in known_fields}
        file_path = obj.get("file", "")
        if path_normalizer is not None:
            file_path = path_normalizer.normalize(file_path)
        rec = DiagnosticRecord(
            file=file_path,
            line=obj.get("line"),
            # mypy 1.11 uses ``column``.  Accept ``col`` as well for captured
            # output from other supported schemas, but do not silently discard it.
            col=obj.get("column", obj.get("col")),
            severity=obj.get("severity", ""),
            message=obj.get("message", ""),
            code=obj.get("code"),
            extras=extras,
            occurrence_index=idx,
        )
        records.append(rec)

    # Attach notes: a "note" immediately following an "error" that has the
    # same file/line is treated as a child of that error.
    top_level: list[DiagnosticRecord] = []
    i = 0
    while i < len(records):
        rec = records[i]
        if rec.severity != "note":
            # Collect following notes that are contextually attached
            j = i + 1
            while j < len(records) and records[j].severity == "note":
                rec.notes.append(records[j])
                j += 1
            top_level.append(rec)
            i = j
        else:
            # Orphaned note (note at start or after another note)
            top_level.append(rec)
            i += 1

    # Recompute occurrence_index on the flat list for stable ordering
    flat_all: list[DiagnosticRecord] = []
    for rec in top_level:
        flat_all.append(rec)
        flat_all.extend(rec.notes)
    for idx, rec in enumerate(flat_all):
        rec.occurrence_index = idx

    error_count = sum(1 for r in top_level if r.severity == "error")

    parse_status = "ok"
    parse_error_detail = ""
    if parse_errors:
        if not raw_records:
            parse_status = "empty_after_error"
        else:
            parse_status = "parse_error"
        parse_error_detail = "; ".join(parse_errors)

    # Distinguish checker crash/configuration failure from an expected type-error
    # exit.  A process failure takes precedence over parse quality.
    if exit_status not in (0, 1):
        parse_status = "checker_crash"
        detail = f"exit_status={exit_status}"
        parse_error_detail = f"{detail}; {parse_error_detail}" if parse_error_detail else detail

    return ParsedDiagnostics(
        records=top_level,
        error_count=error_count,
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_status=exit_status,
        command=command,
        checker_version=checker_version,
        parse_status=parse_status,
        parse_error_detail=parse_error_detail,
    )


# ---------------------------------------------------------------------------
# Path normalizer
# ---------------------------------------------------------------------------

class PathNormalizer:
    """Replaces known host path prefixes with the canonical /task prefix."""

    def __init__(self, host_root: str, task_root: str = "/task") -> None:
        # Normalize separators
        self.host_root = host_root.replace("\\", "/").rstrip("/")
        self.task_root = task_root.rstrip("/")

    def normalize(self, path: str) -> str:
        norm = path.replace("\\", "/")
        if norm.startswith(self.host_root + "/"):
            return self.task_root + norm[len(self.host_root):]
        if norm == self.host_root:
            return self.task_root
        return norm


# ---------------------------------------------------------------------------
# Canonical ordering
# ---------------------------------------------------------------------------

def _loc_sort_key(rec: DiagnosticRecord) -> tuple:
    """Deterministic sort key: file, line (None last), col (None last)."""
    return (
        rec.file,
        rec.line if rec.line is not None else 10**9,
        rec.col if rec.col is not None else 10**9,
        rec.severity,
        rec.message,
        rec.code or "",
        rec.occurrence_index,
    )


def canonical_order(records: list[DiagnosticRecord]) -> list[DiagnosticRecord]:
    """Return top-level records in canonical order. Notes travel with parents."""
    return sorted(records, key=_loc_sort_key)


# ---------------------------------------------------------------------------
# Serialization grammar helpers
# ---------------------------------------------------------------------------
# Use a safe escaping: literal \ → \\, literal newline → \n, | → \|
# This lets us embed arbitrary message text in a single-line field.

def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("|", "\\|")


def _unescape(s: str) -> str:
    result = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "\\":
                result.append("\\")
            elif nxt == "n":
                result.append("\n")
            elif nxt == "|":
                result.append("|")
            else:
                result.append(s[i])
                result.append(nxt)
            i += 2
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


def _fmt_loc(rec: DiagnosticRecord) -> str:
    """file:line:col or file:<no-line>:<no-col> for missing values."""
    line = str(rec.line) if rec.line is not None else "<no-line>"
    col = str(rec.col) if rec.col is not None else "<no-col>"
    return f"{_escape(rec.file)}:{line}:{col}"


def _parse_loc(s: str) -> tuple[str, int | None, int | None]:
    """Inverse of _fmt_loc."""
    parts = s.split(":")
    # File may itself contain colons on Windows; find last two parts
    if len(parts) < 3:
        return _unescape(s), None, None
    col_s = parts[-1]
    line_s = parts[-2]
    file_s = ":".join(parts[:-2])
    line = None if line_s == "<no-line>" else int(line_s)
    col = None if col_s == "<no-col>" else int(col_s)
    return _unescape(file_s), line, col


def _serialize_note(n: DiagnosticRecord) -> str:
    """Single-line, lossless serialization of a note record."""
    return "NOTE|" + json.dumps(n.to_dict(), sort_keys=True, ensure_ascii=False)


def _deserialize_note(s: str) -> DiagnosticRecord:
    if not s.startswith("NOTE|"):
        raise ValueError(f"Bad note line: {s!r}")
    try:
        value = json.loads(s[len("NOTE|"):])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bad note line: {s!r}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Bad note record: {s!r}")
    return DiagnosticRecord.from_dict(value)


# ---------------------------------------------------------------------------
# Expanded renderer
# ---------------------------------------------------------------------------

REPORT_HEADER = "=== TYPE-CHECKER REPORT ==="
# Keep the mode-specific names as aliases for callers that import them; the
# rendered header itself must not disclose the presentation condition.
EXPANDED_HEADER = REPORT_HEADER
EXPANDED_FOOTER_TPL = "--- {error_count} error(s), {total_count} record(s) total ---"
RECORD_SEP = "---"


def render_expanded(parsed: ParsedDiagnostics) -> str:
    """
    One complete diagnostic record per occurrence, in canonical order.
    Each record is a block of key: value lines separated by RECORD_SEP.
    Notes are indented within the parent block.
    """
    lines: list[str] = [EXPANDED_HEADER]
    ordered = canonical_order(parsed.records)
    total = sum(1 + len(r.notes) for r in ordered)
    for rec in ordered:
        lines.append(RECORD_SEP)
        lines.append(f"occurrence_index: {rec.occurrence_index}")
        lines.append(f"severity: {_escape(rec.severity)}")
        lines.append(f"file: {_escape(rec.file)}")
        lines.append(f"line: {rec.line if rec.line is not None else '<no-line>'}")
        lines.append(f"col: {rec.col if rec.col is not None else '<no-col>'}")
        lines.append(f"code: {_escape(rec.code or '')}")
        lines.append(f"message: {_escape(rec.message)}")
        if rec.notes:
            lines.append(f"notes_count: {len(rec.notes)}")
            for n in rec.notes:
                lines.append(f"  {_serialize_note(n)}")
        if rec.extras:
            lines.append(f"extras: {_escape(json.dumps(rec.extras, sort_keys=True))}")
    lines.append(RECORD_SEP)
    lines.append(
        EXPANDED_FOOTER_TPL.format(error_count=parsed.error_count, total_count=total)
    )
    return "\n".join(lines)


def decode_expanded(text: str) -> list[DiagnosticRecord]:
    """
    Decode expanded rendering back to DiagnosticRecord list.
    Raises ValueError on format violations.
    """
    lines = text.splitlines()
    if not lines or lines[0] != EXPANDED_HEADER:
        raise ValueError("Missing expanded header")

    records: list[DiagnosticRecord] = []
    i = 1
    while i < len(lines):
        line = lines[i]
        if line == RECORD_SEP:
            i += 1
            # Check for footer
            if i < len(lines) and lines[i].startswith("--- "):
                break
            # Parse a record block
            rec_fields: dict[str, str] = {}
            notes: list[DiagnosticRecord] = []
            while i < len(lines) and lines[i] != RECORD_SEP:
                l = lines[i]
                if l.startswith("  NOTE|"):
                    notes.append(_deserialize_note(l.strip()))
                    i += 1
                    continue
                if ":" in l:
                    key, _, val = l.partition(": ")
                    rec_fields[key.strip()] = val
                i += 1
            # Build record
            line_val = rec_fields.get("line", "<no-line>")
            col_val = rec_fields.get("col", "<no-col>")
            extras_raw = rec_fields.get("extras", "")
            extras = json.loads(_unescape(extras_raw)) if extras_raw else {}
            rec = DiagnosticRecord(
                file=_unescape(rec_fields.get("file", "")),
                line=None if line_val == "<no-line>" else int(line_val),
                col=None if col_val == "<no-col>" else int(col_val),
                severity=_unescape(rec_fields.get("severity", "")),
                message=_unescape(rec_fields.get("message", "")),
                code=_unescape(rec_fields.get("code", "")) or None,
                notes=notes,
                extras=extras,
                occurrence_index=int(rec_fields.get("occurrence_index", 0)),
            )
            records.append(rec)
        else:
            i += 1
    return records


# ---------------------------------------------------------------------------
# Grouped renderer
# ---------------------------------------------------------------------------

GROUPED_HEADER = REPORT_HEADER
GROUPED_FOOTER_TPL = EXPANDED_FOOTER_TPL
GROUP_SEP = "==="
LOC_PREFIX = "  LOC"


def render_grouped(parsed: ParsedDiagnostics) -> str:
    """
    One complete non-location payload, followed by all occurrence locations.
    Groups only exactly identical non-location payloads (including note context).
    Canonical order within each group; groups ordered by first occurrence.
    """
    ordered = canonical_order(parsed.records)

    # Group by non-location key, preserving insertion order of first occurrence
    from collections import OrderedDict
    groups: OrderedDict[tuple, list[DiagnosticRecord]] = OrderedDict()
    for rec in ordered:
        key = rec.non_location_key()
        groups.setdefault(key, []).append(rec)

    lines: list[str] = [GROUPED_HEADER]
    total = sum(1 + len(r.notes) for r in ordered)

    for key, recs in groups.items():
        lines.append(GROUP_SEP)
        rep = recs[0]  # representative record for payload
        lines.append(f"severity: {_escape(rep.severity)}")
        lines.append(f"code: {_escape(rep.code or '')}")
        lines.append(f"message: {_escape(rep.message)}")
        if rep.notes:
            lines.append(f"notes_count: {len(rep.notes)}")
            for n in rep.notes:
                lines.append(f"  {_serialize_note(n)}")
        if rep.extras:
            lines.append(f"extras: {_escape(json.dumps(rep.extras, sort_keys=True))}")
        lines.append(f"multiplicity: {len(recs)}")
        for rec in recs:
            lines.append(
                f"{LOC_PREFIX}|" + json.dumps(
                    {
                        "file": rec.file,
                        "line": rec.line,
                        "col": rec.col,
                        "occurrence_index": rec.occurrence_index,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )

    lines.append(GROUP_SEP)
    lines.append(
        GROUPED_FOOTER_TPL.format(error_count=parsed.error_count, total_count=total)
    )
    return "\n".join(lines)


def decode_grouped(text: str) -> list[DiagnosticRecord]:
    """
    Decode grouped rendering back to DiagnosticRecord list (in canonical order).
    Raises ValueError on format violations.
    """
    lines = text.splitlines()
    if not lines or lines[0] != GROUPED_HEADER:
        raise ValueError("Missing grouped header")

    records: list[DiagnosticRecord] = []
    i = 1
    while i < len(lines):
        line = lines[i]
        if line == GROUP_SEP:
            i += 1
            if i >= len(lines):
                break
            if lines[i].startswith("--- "):
                break
            # Parse group block
            payload_fields: dict[str, str] = {}
            notes: list[DiagnosticRecord] = []
            locations: list[tuple[str, int | None, int | None, int]] = []
            while i < len(lines) and lines[i] != GROUP_SEP:
                l = lines[i]
                if l.startswith("  NOTE|"):
                    notes.append(_deserialize_note(l.strip()))
                    i += 1
                    continue
                if l.startswith(LOC_PREFIX + "|"):
                    try:
                        loc = json.loads(l[len(LOC_PREFIX) + 1:])
                        if not isinstance(loc, dict):
                            raise TypeError("location is not an object")
                        locations.append((
                            str(loc.get("file", "")),
                            loc.get("line"),
                            loc.get("col"),
                            int(loc["occurrence_index"]),
                        ))
                    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                        raise ValueError(f"Bad location line: {l!r}") from exc
                    i += 1
                    continue
                if ":" in l:
                    key2, _, val = l.partition(": ")
                    payload_fields[key2.strip()] = val
                i += 1
            # Build records from locations
            for (f, ln, cl, occ_idx) in locations:
                rec = DiagnosticRecord(
                    file=f,
                    line=ln,
                    col=cl,
                    severity=_unescape(payload_fields.get("severity", "")),
                    message=_unescape(payload_fields.get("message", "")),
                    code=_unescape(payload_fields.get("code", "")) or None,
                    notes=list(notes),  # copy
                    extras=(
                        json.loads(_unescape(payload_fields["extras"]))
                        if "extras" in payload_fields
                        else {}
                    ),
                    occurrence_index=occ_idx,
                )
                records.append(rec)
        else:
            i += 1

    return canonical_order(records)


# ---------------------------------------------------------------------------
# Round-trip equality verification
# ---------------------------------------------------------------------------

def verify_round_trip(parsed: ParsedDiagnostics) -> None:
    """
    Verify that both renderers decode back to the same canonical record list.
    Raises AssertionError with diagnostic detail if they diverge.
    """
    exp_text = render_expanded(parsed)
    grp_text = render_grouped(parsed)

    exp_decoded = canonical_order(decode_expanded(exp_text))
    grp_decoded = canonical_order(decode_grouped(grp_text))

    # Flatten for comparison (top-level only; notes travel with parents)
    def flatten(recs: list[DiagnosticRecord]) -> list[dict[str, Any]]:
        return [r.to_dict() for r in recs]

    exp_flat = flatten(exp_decoded)
    grp_flat = flatten(grp_decoded)

    if exp_flat != grp_flat:
        import pprint
        raise AssertionError(
            "Round-trip equality failure between expanded and grouped decoders.\n"
            f"Expanded ({len(exp_flat)} records):\n{pprint.pformat(exp_flat[:3])}\n"
            f"Grouped  ({len(grp_flat)} records):\n{pprint.pformat(grp_flat[:3])}"
        )
