from typing import Optional

def get_host() -> Optional[str]:
    return "localhost"

def run_host() -> int:
    h1 = get_host()
    h2 = get_host()
    l1 = len(h1)
    l2 = len(h2)
    return l1 + l2
