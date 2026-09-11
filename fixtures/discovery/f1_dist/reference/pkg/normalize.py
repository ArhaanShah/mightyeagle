from typing import Optional

def normalize(value: float, scale: float) -> float:
    return value / scale if scale != 0 else 0.0

def format_val(val: float) -> str:
    return f"{val:.1f}"

def run_norm() -> None:
    v1 = normalize(10.0, 2.0)
    v2 = normalize(20.0, 4.0)
    format_val(v1)
    format_val(v2)
