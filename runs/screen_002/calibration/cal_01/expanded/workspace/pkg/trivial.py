from typing import Optional

# The function always returns an integer (twice the input). The original
# annotation used ``Optional[int]`` which suggested that ``None`` could be
# returned, causing a type‑checking error when the result is used in an
# arithmetic expression.  The implementation never returns ``None`` so we
# tighten the return type to ``int``.  This preserves the runtime behaviour
# while satisfying the type checker.
def compute(x: int) -> int:
    return x * 2

def run() -> int:
    val = compute(5)
    return val + 1
