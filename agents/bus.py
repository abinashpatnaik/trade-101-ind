"""
agents.bus
==========
Thin Redis wrapper shared by every agent.

Design rules (see plan):
- Authoritative state lives in **keys** (``t101:{MARKET}:state:*``) so a
  restarting agent can cold-read everything it needs before subscribing.
- Pub/sub channels (``t101:{MARKET}:ev:*`` / ``cmd:*``) are *notify-only*;
  missing a message is always safe because consumers re-read state keys.
- Every call is wrapped so a dead Redis never crashes an agent: reads fall
  back to ``None``/defaults, writes are dropped with a warning, and
  ``seconds_since_ok()`` lets callers (the trader) apply fail-safe policies
  such as suppressing new BUYs on a stale bus.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import redis

logger = logging.getLogger(__name__)

_DEFAULT_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

NAMESPACE = "t101"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Bus:
    """Market-scoped Redis bus client."""

    def __init__(self, market: str, redis_url: Optional[str] = None) -> None:
        self.market = market.upper()
        self._url = redis_url or _DEFAULT_URL
        self._redis: Optional[redis.Redis] = None
        self._last_ok: float = 0.0  # monotonic stamp of last successful call
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Key / channel naming
    # ------------------------------------------------------------------

    def key(self, *parts: str) -> str:
        """Build a namespaced key, e.g. key('state', 'session')."""
        return ":".join([NAMESPACE, self.market, *parts])

    def channel(self, *parts: str) -> str:
        return self.key(*parts)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _client(self) -> redis.Redis:
        with self._lock:
            if self._redis is None:
                self._redis = redis.Redis.from_url(
                    self._url,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                    health_check_interval=30,
                )
            return self._redis

    def _mark_ok(self) -> None:
        self._last_ok = time.monotonic()

    def seconds_since_ok(self) -> float:
        """Seconds since the last successful Redis operation.

        Returns +inf if the bus has never been reachable in this process.
        """
        if self._last_ok == 0.0:
            return float("inf")
        return time.monotonic() - self._last_ok

    @property
    def ever_ok(self) -> bool:
        """True if Redis has been reachable at least once in this process."""
        return self._last_ok > 0.0

    def ping(self) -> bool:
        try:
            self._client().ping()
            self._mark_ok()
            return True
        except Exception as exc:
            logger.debug("Bus ping failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # State keys (JSON strings)
    # ------------------------------------------------------------------

    def set_state(self, name: str, value: Dict[str, Any], ex: Optional[int] = None) -> bool:
        """SET t101:{M}:state:{name} to a JSON payload (stamps ts if absent)."""
        payload = dict(value)
        payload.setdefault("ts", _now_iso())
        try:
            self._client().set(self.key("state", name), json.dumps(payload), ex=ex)
            self._mark_ok()
            return True
        except Exception as exc:
            logger.warning("Bus set_state(%s) failed: %s", name, exc)
            return False

    def get_state(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self._client().get(self.key("state", name))
            self._mark_ok()
        except Exception as exc:
            logger.warning("Bus get_state(%s) failed: %s", name, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("Bus get_state(%s): malformed JSON, ignoring", name)
            return None

    def delete_state(self, name: str) -> bool:
        try:
            self._client().delete(self.key("state", name))
            self._mark_ok()
            return True
        except Exception as exc:
            logger.warning("Bus delete_state(%s) failed: %s", name, exc)
            return False

    # ------------------------------------------------------------------
    # Hash state (e.g. the blocklist: field=symbol -> JSON)
    # ------------------------------------------------------------------

    def hset_state(self, name: str, field: str, value: Dict[str, Any]) -> bool:
        try:
            self._client().hset(self.key("state", name), field, json.dumps(value))
            self._mark_ok()
            return True
        except Exception as exc:
            logger.warning("Bus hset_state(%s, %s) failed: %s", name, field, exc)
            return False

    def hdel_state(self, name: str, *fields: str) -> bool:
        if not fields:
            return True
        try:
            self._client().hdel(self.key("state", name), *fields)
            self._mark_ok()
            return True
        except Exception as exc:
            logger.warning("Bus hdel_state(%s) failed: %s", name, exc)
            return False

    def hgetall_state(self, name: str) -> Dict[str, Dict[str, Any]]:
        try:
            raw = self._client().hgetall(self.key("state", name))
            self._mark_ok()
        except Exception as exc:
            logger.warning("Bus hgetall_state(%s) failed: %s", name, exc)
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for field, val in (raw or {}).items():
            try:
                out[field] = json.loads(val)
            except (ValueError, TypeError):
                continue
        return out

    # ------------------------------------------------------------------
    # Simple string markers (idempotency guards)
    # ------------------------------------------------------------------

    def set_marker(self, job: str, value: str) -> bool:
        try:
            self._client().set(self.key("last_run", job), value)
            self._mark_ok()
            return True
        except Exception as exc:
            logger.warning("Bus set_marker(%s) failed: %s", job, exc)
            return False

    def get_marker(self, job: str) -> Optional[str]:
        try:
            val = self._client().get(self.key("last_run", job))
            self._mark_ok()
            return val
        except Exception as exc:
            logger.warning("Bus get_marker(%s) failed: %s", job, exc)
            return None

    # ------------------------------------------------------------------
    # Heartbeats
    # ------------------------------------------------------------------

    def heartbeat(self, agent: str, status: str = "ok", detail: str = "", ttl: int = 90) -> bool:
        payload = {"ts": _now_iso(), "status": status, "detail": detail}
        try:
            self._client().set(self.key("hb", agent), json.dumps(payload), ex=ttl)
            self._mark_ok()
            return True
        except Exception as exc:
            logger.debug("Bus heartbeat(%s) failed: %s", agent, exc)
            return False

    def get_heartbeat(self, agent: str) -> Optional[Dict[str, Any]]:
        try:
            raw = self._client().get(self.key("hb", agent))
            self._mark_ok()
        except Exception as exc:
            logger.debug("Bus get_heartbeat(%s) failed: %s", agent, exc)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Pub/sub
    # ------------------------------------------------------------------

    def publish(self, channel_name: str, payload: Dict[str, Any]) -> bool:
        """Publish to t101:{M}:{channel_name} (e.g. 'ev:trade', 'cmd:scanner')."""
        message = dict(payload)
        message.setdefault("ts", _now_iso())
        try:
            self._client().publish(self.channel(*channel_name.split(":")), json.dumps(message))
            self._mark_ok()
            return True
        except Exception as exc:
            logger.warning("Bus publish(%s) failed: %s", channel_name, exc)
            return False

    def subscribe_forever(
        self,
        channel_names: Iterable[str],
        handler: Callable[[str, Dict[str, Any]], None],
        stop_event: threading.Event,
        poll_seconds: float = 1.0,
    ) -> None:
        """
        Blocking subscribe loop with automatic reconnect.

        ``handler(channel, payload)`` is called for each message; ``channel``
        is the *short* name (namespace prefix stripped). Malformed payloads
        are dropped. Returns when ``stop_event`` is set.
        """
        full_names = [self.channel(*name.split(":")) for name in channel_names]
        prefix = f"{NAMESPACE}:{self.market}:"
        while not stop_event.is_set():
            pubsub = None
            try:
                pubsub = self._client().pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(*full_names)
                self._mark_ok()
                while not stop_event.is_set():
                    message = pubsub.get_message(timeout=poll_seconds)
                    if message is None:
                        continue
                    self._mark_ok()
                    channel = str(message.get("channel", ""))
                    short = channel[len(prefix):] if channel.startswith(prefix) else channel
                    try:
                        payload = json.loads(message.get("data", "{}"))
                    except (ValueError, TypeError):
                        logger.warning("Bus: malformed message on %s, dropped", short)
                        continue
                    try:
                        handler(short, payload)
                    except Exception as exc:
                        logger.error("Bus: handler error on %s: %s", short, exc, exc_info=True)
            except Exception as exc:
                logger.warning("Bus subscribe loop error (%s) — reconnecting in 5s", exc)
                stop_event.wait(5.0)
            finally:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    except Exception:
                        pass
