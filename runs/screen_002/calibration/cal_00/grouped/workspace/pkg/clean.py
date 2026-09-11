def add(a: int, b: int) -> int:
    """Return the sum of two integers.

    The function is deliberately strict about its input types: it only accepts
    instances of :class:`int`.  While ``bool`` is a subclass of ``int`` in Python,
    treating it as a regular integer would be surprising for callers that are
    expecting *numeric* values.  Therefore we explicitly reject ``bool`` as
    well.  This runtime validation aligns the implementation with the static
    type hints and helps the test suite surface type‑related misuse.
    """

    # ``isinstance(True, int)`` returns ``True`` because ``bool`` subclasses
    # ``int``.  We guard against that because the type signature advertises
    # ``int`` – not a boolean flag.
    if isinstance(a, bool) or isinstance(b, bool):
        raise TypeError("bool is not a valid int argument for add()")
    if not isinstance(a, int) or not isinstance(b, int):
        raise TypeError("add() arguments must be int")
    return a + b
