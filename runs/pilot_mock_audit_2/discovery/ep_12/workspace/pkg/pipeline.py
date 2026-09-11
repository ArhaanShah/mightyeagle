from typing import Optional, List

def load_values(path: str) -> List[float]:
    """Load numeric values from a file path."""
    return [1.0, 2.0, 3.0]

def compute_summary(values: List[float]) -> Optional[float]:
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
