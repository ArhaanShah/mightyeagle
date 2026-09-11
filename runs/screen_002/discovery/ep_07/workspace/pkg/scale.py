from typing import Optional, List

def scale_batch(items: List[float], factor: float) -> Optional[List[float]]:
    return [x * factor for x in items]

def run_scale() -> None:
    b = scale_batch([1.0, 2.0], 2.0)
    print(len(b))
