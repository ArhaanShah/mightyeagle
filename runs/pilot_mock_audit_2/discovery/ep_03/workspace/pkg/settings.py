from typing import TypedDict

class DatabaseConfig(TypedDict):
    host: str
    pool_size: str  # should be int
    max_connections: int

class CacheConfig(TypedDict):
    host: str
    timeout: float
    max_size: int

def get_pool_size(cfg: DatabaseConfig) -> int:
    return cfg["pool_size"]  # type error: str not int

def get_timeout(cfg: CacheConfig) -> str:  # should return float (annotation defect)
    return cfg["timeout"]  # returns float at runtime; annotation says str -> mypy error

def merge_configs(db: dict, cache: CacheConfig) -> dict:  # db should be DatabaseConfig
    return {"db_host": db.get("host", ""), "cache_host": cache["host"]}

def create_db_config(host: str, pool_size: int, max_conns: int) -> DatabaseConfig:
    return DatabaseConfig(host=host, pool_size=str(pool_size), max_connections=max_conns)  # type error

def setup_system(db_host: str, cache_host: str) -> dict:
    db: DatabaseConfig = {"host": db_host, "pool_size": "4", "max_connections": 10}
    cache: CacheConfig = {"host": cache_host, "timeout": 5.0, "max_size": 100}
    ps: int = get_pool_size(db)  # type error at assignment
    to: str = get_timeout(cache)
    return merge_configs(db, cache)
