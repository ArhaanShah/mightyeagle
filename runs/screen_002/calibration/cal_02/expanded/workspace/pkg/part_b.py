"""Utilities for part B.

The original code declared :func:`get_b` as returning ``Optional[int]`` even
though it always returns a concrete integer.  This made the static type of the
expression ``get_b() + 1`` become ``int | None`` which is invalid for the ``+``
operator.  By tightening the return type to ``int`` we preserve the runtime
behaviour (the tests expect ``run_b()`` to be ``11``) and satisfy the type
checker.
"""

def get_b() -> int:
    """Return the constant integer ``10``.

    The function never returns ``None``; the annotation reflects that fact.
    """
    return 10


def run_b() -> int:
    """Return ``get_b()`` incremented by one.

    With ``get_b`` now returning ``int`` the addition is type‑safe.
    """
    return get_b() + 1
