from typing import List

def fetch_data() -> str:
    """Return a non‑null string.

    The original annotation allowed ``None`` but the implementation always
    returns the literal ``"item"``. Narrowing the return type to ``str``
    eliminates the ``len`` type errors while preserving the runtime
    behaviour expected by the tests.
    """
    return "item"

def run_many() -> List[int]:
    res = []
    # 8 identical call site errors
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    res.append(len(fetch_data()))
    return res
