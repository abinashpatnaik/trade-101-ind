"""Bus unit tests: key naming, serde, and fail-safe behavior without Redis."""

import json
import math
from unittest import mock

import pytest

from agents.bus import Bus


class FakeRedis:
    """Minimal in-memory stand-in for redis.Redis (decode_responses=True)."""

    def __init__(self):
        self.store = {}
        self.hashes = {}
        self.published = []

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)
        return 1

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def hdel(self, key, *fields):
        h = self.hashes.get(key, {})
        for f in fields:
            h.pop(f, None)
        return len(fields)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    def ping(self):
        return True


@pytest.fixture
def bus():
    b = Bus("IN", "redis://localhost:6399/0")
    b._redis = FakeRedis()
    return b


def test_key_naming(bus):
    assert bus.key("state", "session") == "t101:IN:state:session"
    assert bus.key("hb", "trader") == "t101:IN:hb:trader"
    assert bus.channel("ev", "trade") == "t101:IN:ev:trade"


def test_market_uppercased():
    b = Bus("us", "redis://localhost:6399/0")
    assert b.key("state", "model") == "t101:US:state:model"


def test_state_roundtrip(bus):
    assert bus.set_state("session", {"state": "OPEN", "session_date": "2026-07-14"})
    got = bus.get_state("session")
    assert got["state"] == "OPEN"
    assert got["session_date"] == "2026-07-14"
    assert "ts" in got  # auto-stamped


def test_hash_state_roundtrip(bus):
    bus.hset_state("blocklist", "PNB.NS", {"reason": "hit_rate 25%", "n": 4})
    bus.hset_state("blocklist", "ITC.NS", {"reason": "backtest", "n": 1})
    all_blocked = bus.hgetall_state("blocklist")
    assert set(all_blocked) == {"PNB.NS", "ITC.NS"}
    assert all_blocked["PNB.NS"]["n"] == 4
    bus.hdel_state("blocklist", "PNB.NS")
    assert set(bus.hgetall_state("blocklist")) == {"ITC.NS"}


def test_markers(bus):
    assert bus.get_marker("scanner_premarket") is None
    bus.set_marker("scanner_premarket", "2026-07-14")
    assert bus.get_marker("scanner_premarket") == "2026-07-14"


def test_publish_stamps_ts(bus):
    bus.publish("ev:trade", {"symbol": "ITC.NS", "action": "SELL"})
    channel, raw = bus._redis.published[0]
    assert channel == "t101:IN:ev:trade"
    payload = json.loads(raw)
    assert payload["symbol"] == "ITC.NS"
    assert "ts" in payload


def test_malformed_state_returns_none(bus):
    bus._redis.store[bus.key("state", "strategy")] = "{not json"
    assert bus.get_state("strategy") is None


def test_seconds_since_ok_tracks_success(bus):
    assert math.isinf(bus.seconds_since_ok())
    bus.set_state("session", {"state": "OPEN"})
    assert bus.seconds_since_ok() < 1.0


def test_dead_redis_is_failsafe():
    """With no server listening, every call degrades instead of raising."""
    b = Bus("IN", "redis://localhost:6399/0")  # nothing listens here
    assert b.set_state("session", {"state": "OPEN"}) is False
    assert b.get_state("session") is None
    assert b.hgetall_state("blocklist") == {}
    assert b.publish("ev:trade", {"symbol": "X"}) is False
    assert b.heartbeat("trader") is False
    assert b.ping() is False
    assert math.isinf(b.seconds_since_ok())
