import json
from collections.abc import Awaitable
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from apps.jobs.ports import TransportUnavailable


class RedisTransport:
    def __init__(self, redis: Redis, prefix: str = "ttcp:jobs"):
        self.redis = redis
        self.prefix = prefix

    async def enqueue(self, job_id: UUID) -> None:
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.lpush(self.prefix + ":queue", str(job_id))
                pipe.ltrim(self.prefix + ":queue", 0, 999)
                pipe.expire(self.prefix + ":queue", 60)
                await pipe.execute()
        except RedisError:
            raise TransportUnavailable from None

    async def receive(self) -> UUID | None:
        try:
            # redis-py's command mixin annotates sync and async clients together.
            value = await cast(
                Awaitable[bytes | str | None], self.redis.rpop(self.prefix + ":queue")
            )
        except RedisError:
            raise TransportUnavailable from None
        try:
            return UUID(value.decode() if isinstance(value, bytes) else value) if value else None
        except (ValueError, TypeError, AttributeError):
            return None

    async def publish(self, job_id: UUID, event: dict) -> None:
        key = f"{self.prefix}:log:{job_id}"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.xadd(key, {"event": json.dumps(event)}, maxlen=200, approximate=False)
                pipe.expire(key, 3600)
                await pipe.execute()
        except RedisError:
            raise TransportUnavailable from None

    async def read(self, job_id: UUID, after: int) -> list[dict]:
        try:
            rows = await self.redis.xrange(f"{self.prefix}:log:{job_id}", count=200)
        except RedisError:
            raise TransportUnavailable from None
        events = [json.loads(fields.get(b"event", fields.get("event"))) for _, fields in rows]
        return sorted((e for e in events if e["sequence"] > after), key=lambda e: e["sequence"])
