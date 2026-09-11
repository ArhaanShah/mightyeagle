"""Utilities for part A.

The original implementation annotated ``get_a`` as returning ``Optional[str]``
even though it always returns a concrete string. This caused a type‑checking
error because ``len`` expects a ``Sized`` object, but the static type was
``str | None``. By tightening the return type to ``str`` we keep the runtime
behaviour unchanged while satisfying the type checker.
"""

def get_a() -> str:
    """Return the constant string ``"A"``.

    The function never returns ``None``; the return type reflects that fact.
    """
    return "A"


def run_a() -> int:
    """Return the length of the string produced by :func:`get_a`.

    Because ``get_a`` now returns ``str`` the call to ``len`` is type‑safe.
    """
    return len(get_a())
