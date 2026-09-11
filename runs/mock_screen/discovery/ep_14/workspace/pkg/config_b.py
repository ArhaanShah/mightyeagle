from typing import Optional

def get_port() -> Optional[int]:
    return 8080

def run_port() -> int:
    p = get_port()
    return p + 1
