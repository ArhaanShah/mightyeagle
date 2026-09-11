"""Normalization utilities.

The original implementation incorrectly annotated :func:`normalize` as
returning ``Optional[float]`` even though the function always returns a
``float`` (it returns ``0.0`` when ``scale`` is zero).  This caused callers that
pass the result to :func:`format_val` – which expects a plain ``float`` – to be
type‑checked as receiving ``float | None`` and resulted in mypy ``arg-type``
errors.

We fix the annotation to ``float`` and remove the unused ``Optional`` import.
The runtime behaviour remains identical.
"""

def normalize(value: float, scale: float) -> float:
    """Return ``value / scale`` or ``0.0`` when ``scale`` is zero.

    The function never returns ``None``; it always yields a ``float``.
    """
    return value / scale if scale != 0 else 0.0

def format_val(val: float) -> str:
    return f"{val:.1f}"

def run_norm() -> None:
    v1 = normalize(10.0, 2.0)
    v2 = normalize(20.0, 4.0)
    format_val(v1)
    format_val(v2)
