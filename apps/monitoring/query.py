"""Fixed, bounded VictoriaMetrics queries; no browser-supplied query or URL."""

import asyncio
import json
import math

import httpx

WINDOWS = {"1h": (3600, 60), "6h": (21600, 300), "24h": (86400, 600)}
FRESH_SECONDS = 90
MAX_BYTES = 2_000_000
MAX_SERIES = 1400

METRICS = {
    "cpu_percent": '100 * (1 - avg by (server_id) (rate(node_cpu_seconds_total{mode="idle"}[5m])))',
    "memory_percent": "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
    "load1": "node_load1",
    "disk_percent": (
        "100 * max by (server_id) (1 - "
        'node_filesystem_avail_bytes{fstype!~"tmpfs|devtmpfs|squashfs|overlay"}'
        ' / node_filesystem_size_bytes{fstype!~"tmpfs|devtmpfs|squashfs|overlay"})'
    ),
    "network_receive_bytes_per_second": (
        'sum by (server_id) (rate(node_network_receive_bytes_total{device!="lo"}[5m]))'
    ),
    "network_transmit_bytes_per_second": (
        'sum by (server_id) (rate(node_network_transmit_bytes_total{device!="lo"}[5m]))'
    ),
}
STATUS = {
    "scrape": "ttcp_node_scrape_success",
    "observed": "timestamp(ttcp_node_scrape_success)",
    "probe": "ttcp_service_probe_success",
    "active": "ttcp_service_active",
    "process": "ttcp_process_running",
    "restarts": "ttcp_service_automatic_restarts_total",
    "collector": 'up{job="ttcp-managed-nodes"}',
    "collector_observed": 'timestamp(up{job="ttcp-managed-nodes"})',
}


def combined(expressions):
    # A fixed label preserves metric identity across arithmetic and aggregation.
    return " or ".join(
        f'label_replace(({expression}), "metric", "{key}", "", "")'
        for key, expression in expressions.items()
    )


CURRENT_QUERY = combined(METRICS | STATUS)
# Gate historical metrics by the scrape result and original sample timestamp.
# Range step must never turn an old sample into a current observation.
RECENT_QUERY = combined(
    {
        key: f"({expression}) and on(server_id) (ttcp_node_scrape_success == 1) "
        f"and on(server_id) ((time() - timestamp(ttcp_node_scrape_success)) <= {FRESH_SECONDS})"
        for key, expression in METRICS.items()
    }
)


class MetricsUnavailable(RuntimeError):
    pass


def number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


class MetricsClient:
    def __init__(self, host="victoriametrics", port=8428, timeout=3, *, transport=None):
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            base_url=f"http://{host}:{port}",
            timeout=timeout,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def _query(self, path, parameters, result_type):
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream("GET", path, params=parameters) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_BYTES:
                            raise MetricsUnavailable("Metrics unavailable")
            payload = json.loads(raw)
            data = payload["data"]
            rows = data["result"]
            if (
                payload["status"] != "success"
                or data["resultType"] != result_type
                or not isinstance(rows, list)
                or len(rows) > MAX_SERIES
            ):
                raise MetricsUnavailable("Metrics unavailable")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("metric"), dict):
                    raise MetricsUnavailable("Metrics unavailable")
                if not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in row["metric"].items()
                ):
                    raise MetricsUnavailable("Metrics unavailable")
                values = row.get("values") if result_type == "matrix" else [row.get("value")]
                if not isinstance(values, list) or len(values) > 145:
                    raise MetricsUnavailable("Metrics unavailable")
                for point in values:
                    if not isinstance(point, list) or len(point) != 2 or number(point[0]) is None:
                        raise MetricsUnavailable("Metrics unavailable")
            return rows
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
            # Never propagate upstream bodies, URLs, driver exceptions or labels.
            raise MetricsUnavailable("Metrics unavailable") from None

    async def snapshot(self, window, now):
        duration, step = WINDOWS[window]
        current, recent = await asyncio.gather(
            self._query("/api/v1/query", {"query": CURRENT_QUERY, "time": now}, "vector"),
            self._query(
                "/api/v1/query_range",
                {"query": RECENT_QUERY, "start": now - duration, "end": now, "step": step},
                "matrix",
            ),
        )
        return current, recent
