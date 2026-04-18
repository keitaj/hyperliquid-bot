"""Tests for TTLCacheEntry and TTLCacheMap."""

from ttl_cache import TTLCacheEntry, TTLCacheMap


class TestTTLCacheEntry:

    def test_get_returns_none_when_empty(self):
        cache = TTLCacheEntry(ttl=5.0)
        assert cache.get() is None

    def test_set_and_get(self):
        cache = TTLCacheEntry(ttl=5.0)
        cache.set({"key": "value"})
        assert cache.get() == {"key": "value"}

    def test_expired_returns_none(self):
        cache = TTLCacheEntry(ttl=0.0)
        cache.set("data")
        assert cache.get() is None

    def test_invalidate(self):
        cache = TTLCacheEntry(ttl=5.0)
        cache.set("data")
        cache.invalidate()
        assert cache.get() is None

    def test_overwrite(self):
        cache = TTLCacheEntry(ttl=5.0)
        cache.set("first")
        cache.set("second")
        assert cache.get() == "second"


class TestTTLCacheMap:

    def test_get_returns_none_when_empty(self):
        cache = TTLCacheMap(ttl=5.0)
        assert cache.get("key") is None

    def test_set_and_get(self):
        cache = TTLCacheMap(ttl=5.0)
        cache.set("coin", {"price": 100})
        assert cache.get("coin") == {"price": 100}

    def test_different_keys_independent(self):
        cache = TTLCacheMap(ttl=5.0)
        cache.set("BTC", 50000)
        cache.set("ETH", 3000)
        assert cache.get("BTC") == 50000
        assert cache.get("ETH") == 3000

    def test_expired_returns_none(self):
        cache = TTLCacheMap(ttl=0.0)
        cache.set("key", "data")
        assert cache.get("key") is None

    def test_invalidate_single_key(self):
        cache = TTLCacheMap(ttl=5.0)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate("a")
        assert cache.get("a") is None
        assert cache.get("b") == 2

    def test_invalidate_all(self):
        cache = TTLCacheMap(ttl=5.0)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.invalidate_all()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_tuple_keys(self):
        """Verify tuple keys work (used by HIP-3 caches)."""
        cache = TTLCacheMap(ttl=5.0)
        cache.set(("0xabc", "dex1"), {"balance": 100})
        assert cache.get(("0xabc", "dex1")) == {"balance": 100}
        assert cache.get(("0xabc", "dex2")) is None
