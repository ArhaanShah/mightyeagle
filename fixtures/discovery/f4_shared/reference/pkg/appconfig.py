from typing import TypedDict, List

class AppConfig(TypedDict):
    timeout: int

def get_timeout_primary(cfg: AppConfig) -> int:
    return cfg["timeout"]

def get_timeout_secondary(cfg: AppConfig) -> int:
    return cfg["timeout"]

def run_app() -> List[int]:
    cfg: AppConfig = {"timeout": 30}
    t1 = get_timeout_primary(cfg)
    t2 = get_timeout_secondary(cfg)
    return [t1, t2]
