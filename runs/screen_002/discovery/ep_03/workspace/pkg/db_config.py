from typing import TypedDict

# The database configuration includes a numeric port. The original type
# annotation mistakenly declared the ``port`` field as ``str`` which caused
# static type checkers to report a mismatch when an ``int`` value was
# provided. Updating the TypedDict to use ``int`` aligns the type with the
# actual runtime value while preserving the public API – callers still pass a
# ``DBConfig`` dictionary containing a ``port`` key.
class DBConfig(TypedDict):
    port: int

def get_db_port(cfg: DBConfig) -> int:
    return cfg["port"]

def run_db() -> int:
    cfg: DBConfig = {"port": 5432}
    p1 = get_db_port(cfg)
    p2 = get_db_port(cfg)
    return p1 + p2
