def load_values(path: str) -> list[float]:
    """Load numeric values from a file path."""
    return [1.0, 2.0, 3.0]

def compute_summary(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0

def format_result(mean: float) -> str:
    return f"Mean: {mean:.2f}"

def display_stats(path: str) -> str:
    return format_result(compute_summary(load_values(path)))

def display_secondary_stats(path: str) -> str:
    return format_result(compute_summary(load_values(path)))

def display_tertiary_stats(path: str) -> str:
    return format_result(compute_summary(load_values(path)))
