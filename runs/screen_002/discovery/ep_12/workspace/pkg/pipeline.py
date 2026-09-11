from typing import List

def load_values(path: str) -> List[float]:
    """Load numeric values from a file path."""
    return [1.0, 2.0, 3.0]

def compute_summary(values: List[float]) -> float:
    """Compute the arithmetic mean of *values*.

    The original implementation annotated the return type as
    ``Optional[float]`` even though it never returned ``None`` – the empty
    list case yielded ``0.0``.  Aligning the annotation with the actual
    behaviour removes the need for callers to handle ``None`` and resolves
    the type errors where the result is passed to :func:`format_result`,
    which expects a plain ``float``.
    """
    if not values:
        # Preserve the historic behaviour of returning 0.0 for an empty
        # collection while satisfying the ``float`` return type.
        return 0.0
    total = sum(values)
    return total / len(values)

def format_result(mean: float) -> str:
    return f"Mean: {mean:.2f}"

def display_stats(path: str) -> str:
    vals = load_values(path)
    result = compute_summary(vals)
    label = format_result(result)
    return label

def display_secondary_stats(path: str) -> str:
    vals = load_values(path)
    result = compute_summary(vals)
    label = format_result(result)
    return label

def display_tertiary_stats(path: str) -> str:
    vals = load_values(path)
    result = compute_summary(vals)
    label = format_result(result)
    return label
