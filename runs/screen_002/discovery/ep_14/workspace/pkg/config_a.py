from typing import Optional

# The function always returns a string representing the host.
# The original type hint allowed ``None`` which caused type-checking errors
# when ``len`` was called on the result.  The runtime behavior never returns
# ``None`` (the implementation returns a constant string), so we tighten the
# return type to ``str``.  This preserves the observable behavior while fixing
# the type error.
def get_host() -> str:
    return "localhost"

def run_host() -> int:
    h1 = get_host()
    h2 = get_host()
    l1 = len(h1)
    l2 = len(h2)
    return l1 + l2
