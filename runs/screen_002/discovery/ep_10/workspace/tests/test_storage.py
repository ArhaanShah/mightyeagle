from pkg.storage import CacheBackend, NetworkBackend, LocalBackend, store_data

def test_cache_put_internal():
    b = CacheBackend()
    b.put("k", b"v")
    # Test that put stored the raw bytes internally
    assert b._store["k"] == b"v"

def test_cache_delete():
    b = CacheBackend()
    b.put("k", b"v")
    assert b.delete("k") == True
    assert b.delete("k") == False

def test_network_get_returns_none():
    b = NetworkBackend()
    assert b.get("k") is None

def test_local_put_get():
    b = LocalBackend()
    b.put("k", "hello")  # pass str as annotated
    assert b.get("k") == b"hello"

def test_local_delete():
    b = LocalBackend()
    b.put("k", "x")  # pass str as annotated
    assert b.delete("k")
