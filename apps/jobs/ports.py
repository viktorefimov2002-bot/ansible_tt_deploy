from typing import Protocol
from uuid import UUID


class TransportUnavailable(Exception):
    """An ephemeral transport failed; durable state is unaffected."""


class JobQueue(Protocol):
    async def enqueue(self, job_id: UUID) -> None: ...
    async def receive(self) -> UUID | None: ...


class EventPublisher(Protocol):
    async def publish(self, job_id: UUID, event: dict) -> None: ...
    async def read(self, job_id: UUID, after: int) -> list[dict]: ...
