from typing import Optional

_settings: dict[str, str] = {"host": "localhost", "port": "8080"}

def get_setting(key: str) -> str:  # should be Optional[str], returns None sometimes
    return _settings.get(key)

def resolve_path(base: str, suffix: str) -> str:
    return base.rstrip("/") + "/" + suffix.lstrip("/")

def apply_override(value: str, override: str) -> str:  # should be Optional[str] for override
    if override is not None:
        return override
    return value

def build_url(host_key: str, port_key: str) -> str:
    host = get_setting(host_key)  # annotated str but get_setting may return None
    port = get_setting(port_key)
    return resolve_path(host, port)  # host/port could be None, no guard

def get_connection_info(key: str, default: str) -> str:
    val: Optional[str] = get_setting(key)
    return apply_override(default, val)  # type error: val is Optional[str], override expects str
