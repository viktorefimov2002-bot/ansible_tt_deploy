"""Run the production edge location against a local stub upstream and real NGINX.

Set TTCP_TEST_NGINX to an executable path when nginx is not on PATH.
Only listen ports and the API upstream address differ from the checked-in config.
"""

import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def edge(tmp_path):
    executable = os.environ.get("TTCP_TEST_NGINX") or shutil.which("nginx")
    if not executable:
        pytest.skip("Set TTCP_TEST_NGINX or install nginx for the edge runtime regression")
    received = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append(dict(self.headers))
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = (ROOT / "infra/nginx/default.conf").read_text()
    config = config.replace(
        "listen 8080;", f"listen 127.0.0.1:{port}; listen [::1]:{port} ipv6only=on;"
    )
    config = config.replace("api:8080", f"127.0.0.1:{upstream.server_port}")
    (tmp_path / "logs").mkdir()
    (tmp_path / "temp").mkdir()
    (tmp_path / "nginx.conf").write_text(
        "worker_processes 1; error_log logs/error.log; pid logs/nginx.pid; "
        "events { worker_connections 64; } http { access_log off; " + config + " }"
    )
    args = [executable, "-p", tmp_path.as_posix() + "/", "-c", "nginx.conf"]
    subprocess.run([*args, "-t"], check=True, capture_output=True, timeout=10)
    process = subprocess.Popen(
        [*args, "-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail("NGINX did not start; inspect test error.log")
                time.sleep(0.05)
        yield port, received
    finally:
        subprocess.run([*args, "-s", "quit"], check=False, capture_output=True, timeout=10)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


def test_exchange_limit_uses_actual_tcp_peer_not_spoofed_headers(edge):
    port, received = edge
    transport = httpx.HTTPTransport(local_address="127.0.0.1")
    with httpx.Client(transport=transport, base_url=f"http://127.0.0.1:{port}", timeout=3) as http:
        replies = []
        for n in range(10):
            response = http.post(
                "/api/client/exchange",
                json={"token": "synthetic"},
                headers={
                    "X-Forwarded-For": f"203.0.113.{n}",
                    "X-Real-IP": f"198.51.100.{n}",
                    "Forwarded": f"for=192.0.2.{n}",
                    "X-Forwarded-Host": "evil.test",
                },
            )
            assert response.headers["cache-control"] == "no-store"
            replies.append(response.status_code)
        assert replies[:6] == [401] * 6  # One immediate request plus five in the burst.
        assert replies[6:] == [429] * 4
        assert all(row.get("X-Forwarded-For") == "127.0.0.1" for row in received)
        assert all(row.get("X-Real-IP") == "127.0.0.1" for row in received)
        assert all("Forwarded" not in row and "X-Forwarded-Host" not in row for row in received)
        # This limit does not share the admin login zone or limit session/profile reads.
        assert http.post("/api/auth/login", json={}).status_code == 401
        assert http.get("/api/client/session").status_code == 401
    # Another real socket source has its own bucket, even after the first is exhausted.
    with httpx.Client(
        transport=httpx.HTTPTransport(local_address="::1"),
        base_url=f"http://[::1]:{port}",
        timeout=3,
    ) as second:
        assert second.post("/api/client/exchange", json={}).status_code == 401
    assert received[-1]["X-Forwarded-For"] == "::1"
