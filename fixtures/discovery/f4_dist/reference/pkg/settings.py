from typing import TypedDict

class DatabaseConfig(TypedDict):
    host: str
    pool_size: int  # Fixed: int
    max_connections: int

class CacheConfig(TypedDict):
    host: str
    timeout: float
    max_size: int

def get_pool_size(cfg: DatabaseConfig) -> int:
    return cfg["pool_size"]

def get_timeout(cfg: CacheConfig) -> float:  # Fixed: returns float
    return cfg["timeout"]

def merge_configs(db: DatabaseConfig, cache: CacheConfig) -> dict:  # Fixed: DatabaseConfig
    return {"db_host": db["host"], "cache_host": cache["host"]}

def create_db_config(host: str, pool_size: int, max_conns: int) -> DatabaseConfig:
    return DatabaseConfig(host=host, pool_size=pool_size, max_connections=max_conns)

def setup_system(db_host: str, cache_host: str) -> dict:
    db: DatabaseConfig = {"host": db_host, "pool_size": 4, "max_connections": 10}
    cache: CacheConfig = {"host": cache_host, "timeout": 5.0, "max_size": 100}
    ps: int = get_pool_size(db)
    to: float = get_timeout(cache)
    return merge_configs(db, cache)
