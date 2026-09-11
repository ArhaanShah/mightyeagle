from typing import TypedDict, List

# The application configuration expects a numeric timeout value. The original
# TypedDict incorrectly declared the ``timeout`` field as a ``str`` which caused
# mypy to report type mismatches when the functions returned an ``int`` and when
# the dictionary literal used an ``int`` value. Updating the field type to
# ``int`` aligns the static type information with the runtime behavior and the
# tests.
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
