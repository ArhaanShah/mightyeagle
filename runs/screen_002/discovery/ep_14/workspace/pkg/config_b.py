from typing import Optional

# ``get_port`` always returns a concrete integer port. The original signature
# allowed ``None`` which caused a type error when the result was used in an
# arithmetic expression. Tightening the return type to ``int`` matches the
# implementation and satisfies the type checker without altering runtime
# semantics.
def get_port() -> int:
    return 8080

def run_port() -> int:
    p = get_port()
    return p + 1
