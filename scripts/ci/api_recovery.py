"""Read-only, bounded recovery probe for the already-running disposable API.

Piped into its container by runtime_smoke.sh; never creates a new application pool.
No response body, exception text or connection setting enters diagnostics.
"""

import http.client
import json
import time


def probe(port: int, path: str, timeout: float) -> tuple[int, bool]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = json.loads(response.read(1024))
        expected = "ok" if path == "/healthz" else "ready"
        return response.status, body == {"status": expected}
    except (OSError, http.client.HTTPException, ValueError):
        return 0, False
    finally:
        connection.close()


def wait_ready(port: int = 8080, *, timeout: float = 45, interval: float = 0.2) -> int:
    if not 1 <= port <= 65535 or timeout <= 0 or interval <= 0:
        raise ValueError("Invalid loopback recovery probe")
    started = time.monotonic()
    deadline = started + timeout
    attempts = unavailable = consecutive = 0
    while (remaining := deadline - time.monotonic()) > 0:
        attempts += 1
        status, ready = probe(port, "/readyz", min(12, remaining))
        healthy = status == 200 and ready
        consecutive = consecutive + 1 if healthy else 0
        if consecutive == 2:
            print(
                f"PASS: API eventual recovery attempts={attempts} unavailable={unavailable} "
                f"elapsed_ms={int((time.monotonic() - started) * 1000)}"
            )
            return attempts
        unavailable += int(not healthy)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        live_status, live = probe(port, "/healthz", min(3, remaining))
        if live_status != 200 or not live:
            raise RuntimeError("API liveness failed during dependency recovery") from None
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    raise RuntimeError(
        f"API did not recover before deadline attempts={attempts} unavailable={unavailable}"
    ) from None


if __name__ == "__main__":
    wait_ready()
