"""Real HTTP recovery sequences; persistent failure remains a failed release gate."""

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.ci.api_recovery import wait_ready
from scripts.ci.networking import available_loopback_port


@contextmanager
def api_sequence(statuses, *, live=True, redirect=False):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(self.path)
            if self.path == "/healthz":
                status, value = (200, "ok") if live else (503, "not_ready")
            else:
                status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
                value = "ready" if status == 200 else "not_ready"
            self.send_response(302 if redirect else status)
            if redirect:
                self.send_header("Location", "https://production.invalid/secret-token")
            self.end_headers()
            self.wfile.write(json.dumps({"status": value}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_recovery_polls_real_503_until_two_consecutive_ready_responses(capsys):
    with api_sequence([503, 200, 503, 200, 200]) as (port, observed):
        assert wait_ready(port, timeout=2, interval=0.01) == 5
    assert observed.count("/readyz") == 5
    assert "unavailable=2" in capsys.readouterr().out


def test_persistent_not_ready_is_bounded_failure(capsys):
    with api_sequence([503]) as (port, _):
        with pytest.raises(RuntimeError, match="did not recover before deadline"):
            wait_ready(port, timeout=0.08, interval=0.01)
    assert "PASS" not in capsys.readouterr().out


def test_loss_of_liveness_fails_without_waiting_for_readiness_deadline():
    with api_sequence([503], live=False) as (port, observed):
        with pytest.raises(RuntimeError, match="liveness failed"):
            wait_ready(port, timeout=2, interval=0.01)
    assert observed == ["/readyz", "/healthz"]


def test_recovery_never_follows_redirects_or_logs_body_or_location(capsys):
    with api_sequence([200], redirect=True) as (port, _):
        with pytest.raises(RuntimeError):
            wait_ready(port, timeout=0.08, interval=0.01)
    assert "secret-token" not in capsys.readouterr().out


def test_kernel_selected_explicit_port_is_rebindable_on_ipv4_loopback():
    port = available_loopback_port()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        assert listener.getsockname() == ("127.0.0.1", port)
