from typing import Optional, Protocol

class Backend(Protocol):
    def get(self, key: str) -> Optional[bytes]: ...
    def put(self, key: str, value: bytes) -> None: ...
    def delete(self, key: str) -> bool: ...

class CacheBackend:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
    def get(self, key: str) -> Optional[bytes]:
        return self._store.get(key)
    def put(self, key: str, value: bytes) -> None:
        self._store[key] = value
    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

class NetworkBackend:
    def get(self, key: str) -> Optional[bytes]:
        return None
    def put(self, key: str, value: bytes) -> None:
        pass
    def delete(self, key: str) -> bool:
        return False

class LocalBackend:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
    def get(self, key: str) -> Optional[bytes]:
        return self._data.get(key)
    def put(self, key: str, value: bytes) -> None:
        self._data[key] = value
    def delete(self, key: str) -> bool:
        return bool(self._data.pop(key, None))

def store_data(backend: Backend, key: str, data: bytes) -> bool:
    backend.put(key, data)
    return backend.delete(key)

def retrieve(backend: Backend, key: str) -> Optional[bytes]:
    return backend.get(key)
