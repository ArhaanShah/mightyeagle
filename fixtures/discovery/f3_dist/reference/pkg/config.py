from typing import Optional

_settings: dict[str, str] = {"host": "localhost", "port": "8080"}

def get_setting(key: str) -> Optional[str]:
    return _settings.get(key)

def resolve_path(base: str, suffix: str) -> str:
    return base.rstrip("/") + "/" + suffix.lstrip("/")

def apply_override(value: str, override: Optional[str]) -> str:
    if override is not None:
        return override
    return value

def build_url(host_key: str, port_key: str) -> str:
    host = get_setting(host_key)
    port = get_setting(port_key)
    if host is None or port is None:
        return "http://localhost:8080"
    return resolve_path(host, port)

def get_connection_info(key: str, default: str) -> str:
    val: Optional[str] = get_setting(key)
    return apply_override(default, val)
