from typing import Optional

def compute(x: int) -> Optional[int]:
    return x * 2

def run() -> int:
    val = compute(5)
    return val + 1
