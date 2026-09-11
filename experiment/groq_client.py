"""
groq_client.py — Groq API client with:
  - Disabled SDK automatic retries
  - Per-attempt logging (no secrets)
  - Budget accounting (daily tokens, RPM, per-request)
  - Retry only for explicit retryable HTTP errors
  - quota pause/resume support
  - Full request/response persistence
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import httpx

import groq
from groq import Groq
from groq._exceptions import RateLimitError, APIStatusError, APIConnectionError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Budget configuration (§9)
# ---------------------------------------------------------------------------

# Conservative daily token allowance (actual limit 200,000 but use 180,000)
DEFAULT_DAILY_TOKEN_ALLOWANCE = 180_000
# RPM limit
DEFAULT_RPM = 30
# Max total HTTP attempts across screen_003 (60 generations + 8 retries)
MAX_HTTP_ATTEMPTS = 68
MAX_RETRY_HTTP_ATTEMPTS = 8


def parse_duration_seconds(value: str | float | int | None) -> float | None:
    """Parse provider durations such as ``57.63s`` or ``2m59.56s``."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    matches = re.findall(r"(\d+(?:\.\d+)?)([smh])", text)
    if not matches:
        try:
            return float(text)
        except ValueError:
            return None
    factors = {"s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(number) * factors[unit] for number, unit in matches)


@dataclass
class TokenRateState:
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    reset_at_monotonic: float | None = None
    last_updated_monotonic: float | None = None


# ---------------------------------------------------------------------------
# Attempt record
# ---------------------------------------------------------------------------

@dataclass
class AttemptRecord:
    attempt_number: int
    request_hash: str
    timestamp_utc: str
    http_status: int | None
    request_id: str | None
    outcome: str  # "success" | "rate_limit" | "retryable_error" | "fatal" | "unknown"
    retry_after: float | None
    input_tokens_reserved: int
    output_tokens_reserved: int
    input_tokens_actual: int | None
    output_tokens_actual: int | None
    reasoning_tokens_actual: int | None
    error_detail: str
    quota_headers: dict[str, str] = field(default_factory=dict)
    wait_reason: str | None = None
    wait_seconds: float | None = None


@dataclass
class GenerationRecord:
    episode_id: str
    generation_number: int
    model_id_requested: str
    model_id_returned: str | None
    finish_reason: str | None
    termination_code: str  # per §8 table
    attempts: list[AttemptRecord] = field(default_factory=list)
    raw_response: dict[str, Any] | None = None
    # Content from model
    assistant_content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Usage
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    # Errors
    error_detail: str = ""
    provider_fingerprint: str | None = None


# ---------------------------------------------------------------------------
# Budget tracker
# ---------------------------------------------------------------------------

@dataclass
class BudgetState:
    daily_tokens_used: int = 0
    daily_tokens_reserved: int = 0
    daily_token_allowance: int = DEFAULT_DAILY_TOKEN_ALLOWANCE
    total_http_attempts: int = 0
    max_http_attempts: int = MAX_HTTP_ATTEMPTS
    retry_http_attempts: int = 0
    max_retry_http_attempts: int = MAX_RETRY_HTTP_ATTEMPTS
    # Per-minute tracking (approximate)
    minute_window_start: float = field(default_factory=time.time)
    minute_requests: int = 0
    rpm_limit: int = DEFAULT_RPM
    # Quota window reset
    quota_reset_timestamp: float | None = None
    calibration_generation_slots: int = 0
    discovery_generation_slots: int = 0

    def tokens_remaining(self) -> int:
        return self.daily_token_allowance - self.daily_tokens_used - self.daily_tokens_reserved

    def can_reserve(self, estimated_tokens: int) -> bool:
        return self.tokens_remaining() >= estimated_tokens

    def reserve(self, estimated_tokens: int) -> None:
        self.daily_tokens_reserved += estimated_tokens

    def reconcile(
        self, actual_input: int, actual_output: int, reasoning: int = 0,
        *, reserved: int = 0,
    ) -> None:
        # actual usage replaces reservation
        total = actual_input + actual_output  # don't double-count nested reasoning
        self.daily_tokens_used += total
        self.daily_tokens_reserved = max(0, self.daily_tokens_reserved - reserved)

    def release(self, reserved: int) -> None:
        self.daily_tokens_reserved = max(0, self.daily_tokens_reserved - reserved)

    def can_attempt(self) -> bool:
        return self.total_http_attempts < self.max_http_attempts

    def can_retry(self) -> bool:
        return self.retry_http_attempts < self.max_retry_http_attempts

    def record_attempt(self, is_retry: bool = False) -> None:
        self.total_http_attempts += 1
        if is_retry:
            self.retry_http_attempts += 1
        now = time.time()
        if now - self.minute_window_start >= 60:
            self.minute_window_start = now
            self.minute_requests = 0
        self.minute_requests += 1

    def rpm_wait_seconds(self) -> float:
        """Seconds to wait if near RPM limit."""
        now = time.time()
        elapsed = now - self.minute_window_start
        if elapsed >= 60:
            return 0.0
        if self.minute_requests >= self.rpm_limit - 2:  # conservative buffer
            return 60 - elapsed + 1.0
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def can_record_generation(self, phase: str) -> bool:
        if phase == "calibration":
            return self.calibration_generation_slots < 12
        if phase == "discovery":
            return self.discovery_generation_slots < 48
        return False

    def record_generation(self, phase: str) -> None:
        if not self.can_record_generation(phase):
            raise RuntimeError(f"{phase} generation-slot cap reached")
        if phase == "calibration":
            self.calibration_generation_slots += 1
        else:
            self.discovery_generation_slots += 1


# ---------------------------------------------------------------------------
# Token estimator
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """
    Conservative token estimate: ~3 chars per token with a 1.3x headroom multiplier.
    Calibrated against Groq usage reports during calibration.
    """
    return max(1, int(len(text) / 3 * 1.3))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GroqExperimentClient:
    """
    Groq API client for the experiment.
    - SDK automatic retries are disabled.
    - All attempts are logged.
    - Budget is tracked and enforced.
    - Secrets never appear in logs.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str,
        model_settings: dict[str, Any],
        budget: BudgetState | None = None,
        log_dir: Path | None = None,
        max_retries_per_call: int = 3,
    ) -> None:
        # Disable SDK retries explicitly
        self.client = Groq(api_key=api_key, max_retries=0)
        self.model_id = model_id
        self.model_settings = model_settings
        self.budget = budget or BudgetState()
        self.log_dir = log_dir
        self.max_retries_per_call = max_retries_per_call
        self._attempt_counter = 0
        self.token_rate = TokenRateState()
        self.rate_limit_safety_margin = int(model_settings.get("_rate_limit_safety_margin_tokens", 256))
        self.rate_limit_guard = float(model_settings.get("_rate_limit_guard_seconds", 0.25))
        self.fallback_tpm = int(model_settings.get("_rate_limit_fallback_tpm", 8000))
        self.max_rate_limit_retries = int(model_settings.get("_max_rate_limit_retries_per_generation", 3))

    def _persist_budget(self) -> None:
        if not self.log_dir:
            return
        target = self.log_dir.parent / "budget_state.json"
        tmp = target.with_suffix(f".tmp_{os.getpid()}_{time.time_ns()}")
        tmp.write_text(json.dumps(self.budget.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, target)

    def record_generation_slot(self, phase: str) -> bool:
        if not self.budget.can_record_generation(phase):
            return False
        self.budget.record_generation(phase)
        self._persist_budget()
        return True

    def _log_attempt(self, record: AttemptRecord) -> None:
        logger.info(
            "attempt=%d status=%s outcome=%s request_id=%s",
            record.attempt_number,
            record.http_status,
            record.outcome,
            record.request_id,
        )
        if self.log_dir:
            attempt_file = self.log_dir / f"attempt_{record.attempt_number:04d}.json"
            attempt_file.write_text(
                json.dumps(asdict(record), indent=2), encoding="utf-8"
            )

    def _persist_request(
        self, episode_id: str, generation_number: int,
        attempt_number: int, payload: dict[str, Any],
    ) -> None:
        if not self.log_dir:
            return
        target_dir = self.log_dir / episode_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"gen_{generation_number:02d}_attempt_{attempt_number:02d}_request.json"
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _quota_headers(response: Any) -> dict[str, str]:
        headers = getattr(response, "headers", {}) or {}
        allowed = ("retry-after", "x-ratelimit-", "x-request-id")
        return {
            str(k).lower(): str(v)
            for k, v in headers.items()
            if str(k).lower() == allowed[0]
            or str(k).lower() == allowed[2]
            or str(k).lower().startswith(allowed[1])
        }

    def _update_token_rate(self, headers: dict[str, str]) -> None:
        now = time.monotonic()
        def integer(name: str) -> int | None:
            try:
                return int(headers[name])
            except (KeyError, TypeError, ValueError):
                return None
        limit = integer("x-ratelimit-limit-tokens")
        remaining = integer("x-ratelimit-remaining-tokens")
        reset = parse_duration_seconds(headers.get("x-ratelimit-reset-tokens"))
        if limit is not None:
            self.token_rate.limit_tokens = limit
        if remaining is not None:
            self.token_rate.remaining_tokens = remaining
        if reset is not None:
            self.token_rate.reset_at_monotonic = now + reset
        self.token_rate.last_updated_monotonic = now

    def _pace_tokens(self, reservation: int) -> float:
        state = self.token_rate
        if state.limit_tokens is None and reservation > self.fallback_tpm:
            raise RuntimeError("Single request reservation exceeds fallback TPM limit")
        if state.remaining_tokens is None or state.reset_at_monotonic is None:
            return 0.0
        deficit = reservation + self.rate_limit_safety_margin - state.remaining_tokens
        if deficit <= 0:
            return 0.0
        return max(0.0, state.reset_at_monotonic - time.monotonic()) + self.rate_limit_guard

    def _build_request_payload(
        self,
        messages: list[dict[str, Any]],
        tool_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            **{k: v for k, v in self.model_settings.items() if not k.startswith("_")},
        }
        if tool_schema:
            payload["tools"] = [tool_schema]
        return payload

    def _request_hash(self, payload: dict[str, Any]) -> str:
        # Hash everything except the model key (for comparison)
        stable = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(stable.encode()).hexdigest()[:16]

    def generate(
        self,
        episode_id: str,
        generation_number: int,
        messages: list[dict[str, Any]],
        tool_schema: dict[str, Any] | None = None,
        estimated_input_tokens: int | None = None,
    ) -> GenerationRecord:
        """
        Send one generation request to Groq with retry logic for retryable errors.
        Returns a GenerationRecord regardless of success/failure.
        """
        payload = self._build_request_payload(messages, tool_schema)
        req_hash = self._request_hash(payload)

        if estimated_input_tokens is None:
            # Estimate from message content
            all_text = json.dumps(payload, ensure_ascii=False)
            estimated_input_tokens = estimate_tokens(all_text)

        max_output = self.model_settings.get("max_completion_tokens", 2048)
        estimated_total = estimated_input_tokens + max_output

        record = GenerationRecord(
            episode_id=episode_id,
            generation_number=generation_number,
            model_id_requested=self.model_id,
            model_id_returned=None,
            finish_reason=None,
            termination_code="pending",
        )

        # Check budget before attempting
        if not self.budget.can_reserve(estimated_total):
            record.termination_code = "quota_exhausted"
            record.error_detail = (
                f"Insufficient token budget: need ~{estimated_total}, "
                f"remaining {self.budget.tokens_remaining()}"
            )
            return record

        self.budget.reserve(estimated_total)
        self._persist_budget()

        attempts: list[AttemptRecord] = []
        last_error: str = ""

        for attempt_num in range(1, self.max_retries_per_call + 2):  # +2 = initial + retries
            if attempt_num > 1 and not self.budget.can_retry():
                record.termination_code = "http_attempt_cap"
                record.error_detail = "Retry HTTP attempt cap reached."
                self.budget.release(estimated_total)
                break
            if not self.budget.can_attempt():
                record.termination_code = "http_attempt_cap"
                record.error_detail = "HTTP attempt cap reached."
                self.budget.release(estimated_total)
                break

            # RPM wait
            rpm_wait = self.budget.rpm_wait_seconds()
            if rpm_wait > 0:
                logger.info("RPM limit near, waiting %.1fs", rpm_wait)
                time.sleep(rpm_wait)

            token_wait = self._pace_tokens(estimated_total)
            if token_wait > 0:
                logger.info("TPM limit near, waiting %.2fs", token_wait)
                time.sleep(token_wait)
                self.token_rate.remaining_tokens = None
                self.token_rate.reset_at_monotonic = None

            self._attempt_counter += 1
            self.budget.record_attempt(is_retry=attempt_num > 1)
            self._persist_budget()
            timestamp = datetime.now(timezone.utc).isoformat()

            attempt = AttemptRecord(
                attempt_number=self._attempt_counter,
                request_hash=req_hash,
                timestamp_utc=timestamp,
                http_status=None,
                request_id=None,
                outcome="unknown",
                retry_after=None,
                input_tokens_reserved=estimated_input_tokens,
                output_tokens_reserved=max_output,
                input_tokens_actual=None,
                output_tokens_actual=None,
                reasoning_tokens_actual=None,
                error_detail="",
                wait_reason="token_tpm" if token_wait > 0 else None,
                wait_seconds=token_wait if token_wait > 0 else None,
            )
            self._persist_request(
                episode_id, generation_number, attempt_num, payload
            )

            try:
                # Send request — no automatic retries
                endpoint = self.client.chat.completions
                raw_headers: dict[str, str] = {}
                if hasattr(endpoint, "with_raw_response"):
                    raw = endpoint.with_raw_response.create(**payload)
                    raw_headers = self._quota_headers(raw)
                    self._update_token_rate(raw_headers)
                    response = raw.parse()
                else:
                    response = endpoint.create(**payload)

                attempt.http_status = 200
                attempt.outcome = "success"
                attempt.request_id = getattr(response, "id", None)
                attempt.quota_headers = raw_headers or self._quota_headers(
                    getattr(response, "response", response)
                )
                self._update_token_rate(attempt.quota_headers)

                # Extract usage
                usage = getattr(response, "usage", None)
                if usage:
                    inp = getattr(usage, "prompt_tokens", None)
                    out = getattr(usage, "completion_tokens", None)
                    reasoning = None
                    # Some providers expose reasoning tokens separately
                    ct = getattr(usage, "completion_tokens_details", None)
                    if ct:
                        reasoning = getattr(ct, "reasoning_tokens", None)
                    attempt.input_tokens_actual = inp
                    attempt.output_tokens_actual = out
                    attempt.reasoning_tokens_actual = reasoning

                attempts.append(attempt)
                self._log_attempt(attempt)

                # Reconcile budget
                if (
                    attempt.input_tokens_actual is not None
                    and attempt.output_tokens_actual is not None
                ):
                    self.budget.reconcile(
                        attempt.input_tokens_actual,
                        attempt.output_tokens_actual,
                        attempt.reasoning_tokens_actual or 0,
                        reserved=estimated_total,
                    )
                    record.total_input_tokens = attempt.input_tokens_actual
                    record.total_output_tokens = attempt.output_tokens_actual
                    self._persist_budget()
                else:
                    # Usage unknown: retain the reservation, never record zero.
                    pass

                record.attempts = attempts

                # Parse response
                choice = response.choices[0] if response.choices else None
                if choice is None:
                    record.termination_code = "model_action_invalid"
                    record.error_detail = "Empty choices in response."
                    return record

                # Check model identity
                returned_model = getattr(response, "model", None)
                record.model_id_returned = returned_model

                record.finish_reason = choice.finish_reason
                raw_dict = response.model_dump() if hasattr(response, "model_dump") else {}
                record.raw_response = raw_dict
                record.provider_fingerprint = getattr(response, "system_fingerprint", None)

                # Persist raw response BEFORE executing any action (§14)
                if self.log_dir:
                    response_dir = self.log_dir / episode_id
                    response_dir.mkdir(parents=True, exist_ok=True)
                    resp_file = response_dir / f"gen_{generation_number:02d}_response.json"
                    resp_file.write_text(json.dumps(raw_dict, indent=2), encoding="utf-8")

                msg = choice.message
                record.assistant_content = getattr(msg, "content", None)
                # Extract reasoning (not inserted into assistant content per §6)
                record.reasoning_content = getattr(msg, "reasoning", None)

                # Tool calls
                tc = getattr(msg, "tool_calls", None)
                if tc:
                    record.tool_calls = [
                        {
                            "id": c.id,
                            "type": c.type,
                            "function": {
                                "name": c.function.name,
                                "arguments": c.function.arguments,
                            },
                        }
                        for c in tc
                    ]

                # Classify termination (§8 table)
                record.termination_code = _classify_response(record)
                return record

            except RateLimitError as exc:
                http_status = getattr(exc, "status_code", 429)
                attempt.http_status = http_status
                attempt.outcome = "rate_limit"
                # Parse Retry-After header
                retry_after: float = 60.0
                headers = getattr(exc, "response", None)
                if headers and hasattr(headers, "headers"):
                    attempt.quota_headers = self._quota_headers(headers)
                    self._update_token_rate(attempt.quota_headers)
                    ra = headers.headers.get("Retry-After")
                    if ra:
                        try:
                            retry_after = float(ra)
                        except ValueError:
                            pass
                attempt.retry_after = retry_after
                attempt.error_detail = str(exc)[:200]
                attempts.append(attempt)
                self._log_attempt(attempt)
                last_error = str(exc)

                if attempt_num > self.max_rate_limit_retries:
                    record.attempts = attempts
                    record.termination_code = "rate_limit_censored"
                    record.error_detail = "Rate limit remained unresolved after bounded retries."
                    self.budget.release(estimated_total)
                    self._persist_budget()
                    return record

                logger.warning(
                    "Rate limit on attempt %d, waiting %.1fs", attempt_num, retry_after
                )
                time.sleep(retry_after)
                continue

            except APIConnectionError as exc:
                # Retry only a connection-establishment failure known to precede
                # generation. Read timeouts/disconnects have an unknown outcome.
                cause = exc.__cause__
                if not isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout)):
                    attempt.outcome = "unknown"
                    attempt.error_detail = str(exc)[:200]
                    attempts.append(attempt)
                    self._log_attempt(attempt)
                    record.attempts = attempts
                    record.termination_code = "request_outcome_unknown"
                    record.error_detail = "Connection failed after submission; outcome unknown."
                    return record
                attempt.outcome = "retryable_error"
                attempt.error_detail = str(exc)[:200]
                attempts.append(attempt)
                self._log_attempt(attempt)
                last_error = str(exc)
                wait = min(2 ** attempt_num, 30)
                logger.warning("Connection error, retrying in %ds: %s", wait, exc)
                time.sleep(wait)
                continue

            except APIStatusError as exc:
                http_status = getattr(exc, "status_code", None)
                attempt.http_status = http_status
                response_obj = getattr(exc, "response", None)
                attempt.quota_headers = self._quota_headers(response_obj)
                self._update_token_rate(attempt.quota_headers)
                attempt.error_detail = str(exc)[:200]

                # Content filtering
                if http_status == 451:
                    attempt.outcome = "content_filtered"
                    attempts.append(attempt)
                    self._log_attempt(attempt)
                    record.attempts = attempts
                    record.termination_code = "provider_content_filtered"
                    record.error_detail = str(exc)
                    return record

                if http_status in (500, 502, 503, 504):
                    attempt.outcome = "unknown"
                    attempts.append(attempt)
                    self._log_attempt(attempt)
                    record.attempts = attempts
                    record.termination_code = "request_outcome_unknown"
                    record.error_detail = str(exc)
                    return record

                # Groq reports malformed model-generated tool arguments as a 400.
                # This is a model action observation, not a harness failure.
                body: Any = getattr(exc, "body", None)
                if body is None:
                    response_obj = getattr(exc, "response", None)
                    try:
                        body = response_obj.json() if response_obj is not None else None
                    except Exception:
                        body = None
                error_obj = body.get("error", body) if isinstance(body, dict) else {}
                if isinstance(error_obj, dict) and error_obj.get("code") == "tool_use_failed":
                    attempt.outcome = "model_action_invalid"
                    attempt.error_detail = json.dumps({
                        "type": error_obj.get("type"),
                        "code": error_obj.get("code"),
                        "message": error_obj.get("message"),
                        "failed_generation": error_obj.get("failed_generation"),
                    }, ensure_ascii=False)[:4000]
                    attempts.append(attempt)
                    self._log_attempt(attempt)
                    if self.log_dir:
                        error_dir = self.log_dir / episode_id
                        error_dir.mkdir(parents=True, exist_ok=True)
                        (error_dir / f"gen_{generation_number:02d}_provider_error.json").write_text(
                            json.dumps({
                                "http_status": http_status,
                                "request_hash": req_hash,
                                "generation_number": generation_number,
                                "error": {
                                    key: error_obj.get(key)
                                    for key in ("type", "code", "message", "failed_generation")
                                },
                                "rate_limit_headers": attempt.quota_headers,
                            }, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                    record.attempts = attempts
                    record.termination_code = "model_action_invalid"
                    record.error_detail = attempt.error_detail
                    self.budget.release(estimated_total)
                    self._persist_budget()
                    return record

                # 400 with model-produced malformed tool: NOT a generic retry (§9)
                attempt.outcome = "fatal"
                attempts.append(attempt)
                self._log_attempt(attempt)
                record.attempts = attempts
                record.termination_code = "technical_failure"
                record.error_detail = str(exc)
                self.budget.release(estimated_total)
                self._persist_budget()
                return record

            except (TypeError, ValueError) as exc:
                # SDK-side request construction/validation failed before an HTTP
                # request could be submitted, so this is a known technical
                # failure and the reservation can safely be released.
                attempt.outcome = "fatal"
                attempt.error_detail = str(exc)[:200]
                attempts.append(attempt)
                self._log_attempt(attempt)
                record.attempts = attempts
                record.termination_code = "technical_failure"
                record.error_detail = f"Local request validation failed: {exc}"
                self.budget.release(estimated_total)
                self._persist_budget()
                return record

            except Exception as exc:
                # Unknown error — outcome uncertain (§9)
                attempt.outcome = "unknown"
                attempt.error_detail = str(exc)[:200]
                attempts.append(attempt)
                self._log_attempt(attempt)
                record.attempts = attempts
                record.termination_code = "request_outcome_unknown"
                record.error_detail = (
                    f"Unknown error after submission — remote execution count uncertain: {exc}"
                )
                return record

        # Exhausted retries
        record.attempts = attempts
        record.termination_code = "technical_failure"
        record.error_detail = f"Exhausted {self.max_retries_per_call + 1} attempts. Last: {last_error}"
        self.budget.release(estimated_total)
        self._persist_budget()
        return record


# ---------------------------------------------------------------------------
# Response classifier (§8 table)
# ---------------------------------------------------------------------------

def _classify_response(gen: GenerationRecord) -> str:
    """Map a received response to the §8 termination code."""
    # Model identity mismatch
    if (
        gen.model_id_returned
        and gen.model_id_requested
        and gen.model_id_returned != gen.model_id_requested
    ):
        # Allow model ID variations (e.g., "openai/gpt-oss-120b" vs "gpt-oss-120b")
        # Strip provider prefix for comparison
        returned_base = gen.model_id_returned.split("/")[-1]
        requested_base = gen.model_id_requested.split("/")[-1]
        if returned_base != requested_base:
            return "model_identity_failure"

    # Length truncation
    if gen.finish_reason in ("length", "max_tokens"):
        return "token_censored"

    # Content filtering
    if gen.finish_reason == "content_filter":
        return "provider_content_filtered"

    # Tool call
    tc = gen.tool_calls
    if tc:
        if len(tc) == 1 and tc[0].get("function", {}).get("name") == "edit_and_check":
            return "tool_call_received"  # will be executed by runner
        elif len(tc) > 1:
            return "model_action_invalid"  # multiple tool calls
        elif tc[0].get("function", {}).get("name") != "edit_and_check":
            return "model_action_invalid"  # unknown tool
        else:
            return "tool_call_received"

    # Final content (no tool call)
    content = gen.assistant_content
    if content and content.strip():
        return "model_final"

    # Empty content, no tool call
    return "model_action_invalid"
