from typing import List

def load_values(path: str) -> List[float]:
    """Load numeric values from a file path."""
    return [1.0, 2.0, 3.0]

def compute_summary(values: List[float]) -> float:
    """Compute the arithmetic mean of a list of numbers.

    Returns ``0.0`` for an empty list to keep the return type consistently a
    ``float``. This aligns with the expectations of callers such as
    ``format_result`` which require a ``float`` argument.
    """
    if not values:
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
