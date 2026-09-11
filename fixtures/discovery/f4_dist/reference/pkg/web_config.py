from typing import TypedDict

class WebConfig(TypedDict):
    workers: int

def get_workers(cfg: WebConfig) -> int:
    return cfg["workers"]

def run_web() -> int:
    cfg: WebConfig = {"workers": 4}
    return get_workers(cfg)
