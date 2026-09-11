"""Configuration utilities for port handling.

The original ``get_port`` function was annotated as returning ``Optional[int]``
even though it always returns a concrete integer.  When the result is used in
arithmetic (``p + 1``) the type checker rightfully flags a potential ``None``
operand.  To preserve the runtime semantics while satisfying static analysis we
tighten the annotation to ``int``.
"""

from typing import Optional


def get_port() -> int:
    """Return the port number.

    The function is intentionally straightforward for the test suite – it
    always returns ``8080``.
    """
    return 8080

def run_port() -> int:
    p = get_port()
    return p + 1
