from typing import TypedDict

class DBConfig(TypedDict):
    port: int

def get_db_port(cfg: DBConfig) -> int:
    return cfg["port"]

def run_db() -> int:
    cfg: DBConfig = {"port": 5432}
    p1 = get_db_port(cfg)
    p2 = get_db_port(cfg)
    return p1 + p2
