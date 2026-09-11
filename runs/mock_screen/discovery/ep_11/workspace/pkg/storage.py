from typing import Optional, Protocol

class Backend(Protocol):
    def get(self, key: str) -> Optional[bytes]: ...
    def put(self, key: str, value: bytes) -> None: ...
    def delete(self, key: str) -> bool: ...

class CacheBackend:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
    def get(self, key: str) -> int:  # wrong return type: should be Optional[bytes]
        data = self._store.get(key)
        return len(data) if data else 0
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
    # delete method is missing

class LocalBackend:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
    def get(self, key: str) -> Optional[bytes]:
        return self._data.get(key)
    def put(self, key: str, value: str) -> None:  # wrong param type: value should be bytes
        self._data[key] = value.encode()
    def delete(self, key: str) -> bool:
        return bool(self._data.pop(key, None))

# Protocol conformance checks - these trigger mypy errors
_cache_check: Backend = CacheBackend()   # type error: CacheBackend.get returns int not Optional[bytes]
_network_check: Backend = NetworkBackend()  # type error: missing delete
_local_check: Backend = LocalBackend()   # type error: put value param is str not bytes

def store_data(backend: Backend, key: str, data: bytes) -> bool:
    backend.put(key, data)
    return backend.delete(key)

def retrieve(backend: Backend, key: str) -> Optional[bytes]:
    return backend.get(key)
