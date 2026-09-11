from typing import TypedDict

class AppConfig(TypedDict):
    timeout: int

def get_timeout_primary(cfg: AppConfig) -> int:
    return cfg["timeout"]

def get_timeout_secondary(cfg: AppConfig) -> int:
    return cfg["timeout"]

def run_app() -> list[int]:
    cfg: AppConfig = {"timeout": 30}
    return [get_timeout_primary(cfg), get_timeout_secondary(cfg)]
