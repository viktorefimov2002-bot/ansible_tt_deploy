"""Read-only operational summaries from durable inventory and private metrics."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from apps.monitoring.query import FRESH_SECONDS, METRICS, WINDOWS, MetricsUnavailable, number
from apps.persistence.database import transaction
from apps.persistence.models import Server


def instant(timestamp):
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def fresh(timestamp, now):
    return timestamp is not None and 0 <= now - timestamp <= FRESH_SECONDS


class MonitoringService:
    def __init__(self, engine, dependencies, metrics):
        self.engine = engine
        self.dependencies = dependencies
        self.metrics = metrics

    async def inventory(self):
        # Select only public inventory fields, never load encrypted SSH identities.
        async with transaction(self.engine) as db:
            rows = await db.execute(
                select(
                    Server.id,
                    Server.name,
                    Server.enabled,
                    Server.status,
                    Server.last_seen_at,
                ).order_by(Server.name, Server.id)
            )
            return list(rows)

    async def summary(self, window, *, now=None):
        now = now or datetime.now(UTC)
        timestamp = now.timestamp()
        inventory, checks, samples = await asyncio.gather(
            self.inventory(),
            self.dependencies.check(),
            self._samples(window, timestamp),
        )
        current, recent, available = samples
        latest = {}
        for row in current:
            metric = row["metric"]
            key = (metric.get("server_id"), metric.get("metric"))
            latest[key] = number(row["value"][1])
        collector_time = latest.get((None, "collector_observed"))
        collector_value = latest.get((None, "collector"))
        collector = (
            "unknown"
            if not available or collector_value is None
            else "healthy"
            if fresh(collector_time, timestamp) and collector_value == 1
            else "unavailable"
        )
        history = self._history(recent, timestamp, window)
        servers = []
        for node in inventory:
            server_id = str(node.id)
            values = {
                key: value for (identity, key), value in latest.items() if identity == server_id
            }
            observed = values.get("observed")
            reachable = (
                collector == "healthy" and fresh(observed, timestamp) and values.get("scrape") == 1
            )
            probe = values.get("probe") if reachable else None
            service_known = probe == 1
            active = values.get("active") if service_known else None
            process = values.get("process") if service_known else None
            health = (
                "disabled"
                if not node.enabled
                else "unknown"
                if not available or collector == "unknown" or observed is None
                else "unavailable"
                if not reachable
                else "healthy"
                if service_known and active == 1 and process == 1
                else "degraded"
            )
            servers.append(
                {
                    "id": server_id,
                    "name": node.name,
                    "enabled": node.enabled,
                    "management_status": node.status,
                    "last_seen_at": node.last_seen_at,
                    "health": health,
                    "observed_at": instant(observed) if node.enabled else None,
                    "metrics": {
                        key: self._metric(key, values.get(key))
                        if reachable and node.enabled
                        else None
                        for key in METRICS
                    },
                    "service": {
                        "probe": "healthy"
                        if service_known
                        else "unavailable"
                        if probe == 0
                        else "unknown",
                        "active": active == 1 if active in (0, 1) else None,
                        "process_running": process == 1 if process in (0, 1) else None,
                        "automatic_restarts": values.get("restarts") if service_known else None,
                    }
                    if node.enabled
                    else {
                        "probe": "unknown",
                        "active": None,
                        "process_running": None,
                        "automatic_restarts": None,
                    },
                    "recent": history.get(server_id, []),
                }
            )
        healthy = all(checks.values()) and available and collector == "healthy"
        healthy = healthy and all(node["health"] in ("healthy", "disabled") for node in servers)
        return {
            "generated_at": now.isoformat(),
            "window": window,
            "step_seconds": WINDOWS[window][1],
            "system": {
                "status": "healthy" if healthy else "degraded",
                "postgres": "healthy" if checks.get("postgres") else "unavailable",
                "redis": "healthy" if checks.get("redis") else "unavailable",
                "victoriametrics": "healthy" if available else "unavailable",
                "collector": collector,
            },
            "servers": servers,
        }

    async def _samples(self, window, now):
        try:
            current, recent = await self.metrics.snapshot(window, now)
            return current, recent, True
        except MetricsUnavailable:
            return [], [], False

    @staticmethod
    def _metric(key, value):
        if value is None or value < 0 or (key.endswith("percent") and value > 100):
            return None
        return value

    def _history(self, rows, now, window):
        duration, step = WINDOWS[window]
        start = now - duration
        series = {}
        for row in rows:
            server_id = row["metric"].get("server_id")
            key = row["metric"].get("metric")
            if not isinstance(server_id, str) or key not in METRICS:
                continue
            points = series.setdefault(server_id, {})
            for timestamp, value in row["values"]:
                timestamp = number(timestamp)
                if timestamp is None or not start <= timestamp <= now:
                    continue
                index = round((timestamp - start) / step)
                if abs(start + index * step - timestamp) > 0.01:
                    continue
                point = points.setdefault(index, dict.fromkeys(METRICS))
                point[key] = self._metric(key, number(value))
        return {
            server_id: [
                dict(at=instant(start + index * step), **points.get(index, dict.fromkeys(METRICS)))
                for index in range(duration // step + 1)
            ]
            for server_id, points in series.items()
            if points
        }
