"""Run the production edge location against a local stub upstream and real NGINX.

Set TTCP_TEST_NGINX to an executable path when nginx is not on PATH.
Only listen ports and the API upstream address differ from the checked-in config.
"""

import asyncio
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from apps.api.main import create_app
from apps.servers.service import ServerService
from tests.test_auth import account, auth, key  # noqa: F401
from tests.test_migrations import database  # noqa: F401
from tests.test_servers import payload

ROOT = Path(__file__).resolve().parents[1]


class UpstreamRequests(list):
    """Record edge forwarding, optionally dispatching requests to the real API."""

    backend = None


@pytest.fixture
def edge(tmp_path, request):
    executable = os.environ.get("TTCP_TEST_NGINX") or shutil.which("nginx")
    if not executable:
        pytest.skip("Set TTCP_TEST_NGINX or install nginx for the edge runtime regression")
    received = UpstreamRequests()

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append(dict(self.headers))
            if received.backend is not None:
                response = received.backend(self.command, self.path, dict(self.headers), body)
                self.send_response(response.status_code)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response.content)
                return
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST
        do_HEAD = do_POST
        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def expand(name):
        config = (ROOT / "infra/nginx" / name).read_text()
        return re.sub(r"include /etc/nginx/ttcp/([^;]+);", lambda match: expand(match[1]), config)

    production = getattr(request, "param", None) == "production"
    config = expand("production.conf.template" if production else "default.conf")
    if production:
        config = config.replace("${TTCP_CLIENT_HOST}", "vpn.localhost")
        config = config.replace("${TTCP_ADMIN_HOST}", "admin.localhost")
        # Test certificate material is temporary and never enters the repository.
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        (tmp_path / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (tmp_path / "key.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        for role in ("admin", "client"):
            config = config.replace(
                f"/etc/nginx/tls/{role}/fullchain.pem", (tmp_path / "cert.pem").as_posix()
            ).replace(f"/etc/nginx/tls/{role}/privkey.pem", (tmp_path / "key.pem").as_posix())
        with socket.socket() as http_socket, socket.socket() as health_socket:
            http_socket.bind(("127.0.0.1", 0))
            health_socket.bind(("127.0.0.1", 0))
            config = config.replace(
                "listen 8080",
                f"listen 127.0.0.1:{health_socket.getsockname()[1]}",
            )
            config = config.replace("listen 80", f"listen 127.0.0.1:{http_socket.getsockname()[1]}")
        config = config.replace("listen 443", f"listen 127.0.0.1:{port}")
    config = config.replace("listen 8080", f"listen 127.0.0.1:{port}")
    # IPv6 listener is declared only once on the default virtual host.
    config = config.replace(
        f"listen 127.0.0.1:{port} default_server;",
        f"listen 127.0.0.1:{port} default_server; listen [::1]:{port} ipv6only=on;",
    )
    config = config.replace(
        f"listen 127.0.0.1:{port};", f"listen 127.0.0.1:{port}; listen [::1]:{port};"
    )
    config = config.replace("api:8080", f"127.0.0.1:{upstream.server_port}")
    (tmp_path / "logs").mkdir()
    (tmp_path / "temp").mkdir()
    (tmp_path / "nginx.conf").write_text(
        "worker_processes 1; error_log logs/error.log; pid logs/nginx.pid; "
        "events { worker_connections 64; } http { access_log off; " + config + " }"
    )
    args = [executable, "-p", tmp_path.as_posix() + "/", "-c", "nginx.conf"]
    syntax = subprocess.run([*args, "-t"], capture_output=True, timeout=10)
    assert syntax.returncode == 0, syntax.stderr.decode(errors="replace")
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
    with httpx.Client(
        transport=transport,
        base_url=f"http://127.0.0.1:{port}",
        headers={"Host": "vpn.localhost"},
        timeout=3,
    ) as http:
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
        assert (
            http.post("/api/auth/login", json={}, headers={"Host": "admin.localhost"}).status_code
            == 401
        )
        assert http.get("/api/client/session").status_code == 401
    # Another real socket source has its own bucket, even after the first is exhausted.
    with httpx.Client(
        transport=httpx.HTTPTransport(local_address="::1"),
        base_url=f"http://[::1]:{port}",
        headers={"Host": "vpn.localhost"},
        timeout=3,
    ) as second:
        assert second.post("/api/client/exchange", json={}).status_code == 401
    assert received[-1]["X-Forwarded-For"] == "::1"


@pytest.mark.parametrize("host,other", [("vpn.localhost", "admin"), ("admin.localhost", "client")])
def test_origins_cannot_reach_each_others_api_even_with_credentials(edge, host, other):
    port, received = edge
    identity = "11111111-1111-1111-1111-111111111111"
    admin_routes = [
        ("POST", "/api/auth/login"),
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/servers"),
        ("POST", f"/api/servers/{identity}/preflight"),
        ("POST", f"/api/servers/{identity}/status"),
        *(
            ("POST", f"/api/servers/{identity}/{operation}")
            for operation in ("deploy", "update", "restart", "uninstall")
        ),
        ("GET", f"/api/servers/{identity}/config-revisions"),
        ("POST", f"/api/servers/{identity}/config-revisions"),
        ("GET", f"/api/servers/{identity}/config-revisions/{identity}"),
        ("POST", f"/api/servers/{identity}/config-revisions/{identity}/apply"),
        ("GET", "/api/vpn-users"),
        ("GET", f"/api/vpn-users/{identity}/devices"),
        ("GET", f"/api/vpn-users/{identity}/invitations"),
        ("GET", "/api/jobs"),
        ("GET", f"/api/jobs/{identity}"),
        ("GET", f"/api/jobs/{identity}/logs"),
        ("POST", f"/api/jobs/{identity}/cancel"),
        ("GET", "/api/monitoring"),
        ("GET", "/api/audit"),
        ("GET", "/api/notifications"),
        ("PUT", f"/api/notifications/{identity}/read"),
    ]
    client_routes = [
        ("POST", "/api/client/exchange"),
        ("GET", "/api/client/session"),
        ("GET", "/api/client/me"),
        ("GET", "/api/client/devices"),
        ("GET", "/api/client/servers"),
        ("POST", "/api/client/logout"),
        ("GET", f"/api/client/devices/{identity}/credentials"),
        ("GET", f"/api/client/devices/{identity}/provisioning"),
        ("POST", f"/api/client/devices/{identity}/configurations/{identity}"),
    ]
    opposite = admin_routes if other == "admin" else client_routes
    own = client_routes if other == "admin" else admin_routes
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as http:
        headers = {
            "Host": host,
            "Origin": "http://" + host,
            "Authorization": "Bearer " + "A" * 43,
            "Cookie": "__Host-ttcp_admin=" + "A" * 43 + "; __Secure-ttcp_client=" + "C" * 43,
            "X-TTCP-Admin": "web",
            "X-TTCP-Client": "portal",
            "X-Forwarded-Host": "admin.localhost" if other == "admin" else "vpn.localhost",
        }
        before = len(received)
        for method, path in opposite:
            response = http.request(method, path, headers=headers)
            assert response.status_code == 404, (host, path, response.text)
            assert "access-control-allow-origin" not in response.headers
            assert response.headers["cache-control"] == "no-store"
        assert len(received) == before
        for method, path in own:
            assert http.request(method, path, headers=headers).status_code == 401, path
        assert len(received) == before + len(own)
        for path in (
            "/api/unknown",
            "/api/auth/sessions/" + identity,
            "/client/",
            "/admin/",
        ):
            assert http.get(path, headers=headers).status_code == 404
        assert http.get("/api/jobs", headers={"Host": "evil.test"}).status_code == 404


def test_public_edge_does_not_grant_cors_or_unsupported_methods(edge):
    port, received = edge
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as http:
        before = len(received)
        for host, path in (("admin.localhost", "/api/jobs"), ("vpn.localhost", "/api/client/me")):
            response = http.options(
                path,
                headers={
                    "Host": host,
                    "Origin": "https://evil.test",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert response.status_code == 403
            assert "access-control-allow-origin" not in response.headers
            assert "access-control-allow-credentials" not in response.headers
        assert len(received) == before


@pytest.mark.parametrize("edge", ["development", "production"], indirect=True)
def test_lifecycle_post_routes_are_exact_and_admin_only(edge, request):
    port, received = edge
    production = request.node.callspec.params["edge"] == "production"
    scheme = "https" if production else "http"
    identity = "11111111-1111-1111-1111-111111111111"
    routes = [
        f"/api/servers/{identity}/{name}" for name in ("deploy", "update", "restart", "uninstall")
    ]

    def origin(host):
        headers = {
            "Host": host,
            "Authorization": "Bearer " + "A" * 43,
            "Cookie": "__Host-ttcp_admin=" + "A" * 43,
        }
        extensions = {"sni_hostname": host} if production else {}
        return {"headers": headers, "extensions": extensions}

    with httpx.Client(base_url=f"{scheme}://127.0.0.1:{port}", verify=False, timeout=3) as http:
        before = len(received)
        for path in routes:
            allowed = http.post(
                path, json={"idempotency_key": "edge-regression"}, **origin("admin.localhost")
            )
            assert allowed.status_code == 401, path
            assert allowed.headers["cache-control"] == "no-store"
        assert len(received) == before + len(routes)
        before = len(received)
        for path in routes:
            for method in ("GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"):
                denied = http.request(method, path, **origin("admin.localhost"))
                assert denied.status_code == 403, (method, path)
                assert "access-control-allow-origin" not in denied.headers
            for method in ("POST", "GET", "PUT", "DELETE", "OPTIONS"):
                denied = http.request(method, path, **origin("vpn.localhost"))
                assert denied.status_code == 404, (method, path)
        for path in (
            f"/api/servers/{identity}/deploy/extra",
            f"/api/servers/{identity}/restart-all",
            f"/api/servers/{identity}/execute",
            f"/api/servers/{identity}/playbook",
            "/api/servers/invalid/deploy",
            "/api/servers/" + "-" * 36 + "/deploy",
            "/api/servers/" + "1" * 36 + "/deploy",
        ):
            denied = http.post(path, **origin("admin.localhost"))
            assert denied.status_code == 404, path
        assert len(received) == before


@pytest.mark.parametrize("edge", ["production"], indirect=True)
def test_production_tls_hosts_use_disjoint_api_allowlists(edge):
    port, received = edge
    identity = "11111111-1111-1111-1111-111111111111"
    with httpx.Client(base_url=f"https://127.0.0.1:{port}", verify=False, timeout=3) as http:
        for host, own, opposite in (
            (
                "vpn.localhost",
                "/api/client/me",
                [
                    "/api/auth/me",
                    "/api/jobs",
                    "/api/servers",
                    "/api/vpn-users",
                    f"/api/jobs/{identity}/logs",
                    "/api/monitoring",
                    "/api/audit",
                    "/api/notifications",
                    f"/api/notifications/{identity}/read",
                ],
            ),
            (
                "admin.localhost",
                "/api/auth/me",
                [
                    "/api/client/me",
                    "/api/client/session",
                    "/api/client/devices",
                    "/api/client/servers",
                ],
            ),
        ):
            headers = {"Host": host, "Authorization": "Bearer " + "A" * 43}
            extensions = {"sni_hostname": host}
            allowed = http.request("GET", own, headers=headers, extensions=extensions)
            assert allowed.status_code == 401
            assert allowed.headers["strict-transport-security"] == "max-age=31536000"
            assert "connect-src 'self'" in allowed.headers["content-security-policy"]
            before = len(received)
            for path in opposite:
                denied = http.request("GET", path, headers=headers, extensions=extensions)
                assert denied.status_code == 404
                assert "access-control-allow-origin" not in denied.headers
            assert len(received) == before


@pytest.mark.parametrize("edge", ["development", "production"], indirect=True)
def test_visibility_allowlist_is_exact_and_restricted_to_supported_methods(edge, request):
    port, received = edge
    production = request.node.callspec.params["edge"] == "production"
    scheme = "https" if production else "http"
    identity = "11111111-1111-1111-1111-111111111111"
    headers = {"Host": "admin.localhost", "Authorization": "Bearer " + "A" * 43}
    extensions = {"sni_hostname": "admin.localhost"} if production else {}
    reads = (
        "/api/monitoring?window=6h",
        "/api/audit?limit=2&offset=2&result=denied",
        "/api/notifications?limit=2&offset=2&unread_only=true",
    )
    receipt = f"/api/notifications/{identity}/read"
    with httpx.Client(base_url=f"{scheme}://127.0.0.1:{port}", verify=False, timeout=3) as http:
        before = len(received)
        for path in reads:
            allowed = http.request("GET", path, headers=headers, extensions=extensions)
            assert allowed.status_code == 401, path
            assert allowed.headers["cache-control"] == "no-store"
        allowed = http.request(
            "PUT", receipt, json={"read": True}, headers=headers, extensions=extensions
        )
        assert allowed.status_code == 401
        assert allowed.headers["cache-control"] == "no-store"
        assert len(received) == before + len(reads) + 1
        before = len(received)
        for path, methods in (
            *((path, ("POST", "PUT", "PATCH", "DELETE", "OPTIONS")) for path in reads),
            (receipt, ("GET", "POST", "PATCH", "DELETE", "OPTIONS")),
        ):
            for method in methods:
                denied = http.request(method, path, headers=headers, extensions=extensions)
                assert denied.status_code == 403, (method, path)
                assert "access-control-allow-origin" not in denied.headers
                assert denied.headers["cache-control"] == "no-store"
        for path in (
            "/api/monitoring/query",
            "/api/audit/" + identity,
            "/api/notifications/" + identity,
            receipt + "/extra",
            "/api/notifications/invalid/read",
            f"/api/notifications/{identity}/unread",
            "/api/v1/query",
            "/api/metrics",
        ):
            denied = http.request("GET", path, headers=headers, extensions=extensions)
            assert denied.status_code == 404, path
        assert len(received) == before


@pytest.mark.parametrize("edge", ["development", "production"], indirect=True)
def test_revision_routes_are_exact_and_admin_only(edge, request):
    port, received = edge
    production = request.node.callspec.params["edge"] == "production"
    scheme = "https" if production else "http"
    identity = "11111111-1111-1111-1111-111111111111"
    collection = f"/api/servers/{identity}/config-revisions"
    detail = collection + "/" + identity
    routes = (
        (collection, ("GET", "HEAD", "POST")),
        (detail, ("GET", "HEAD")),
        (detail + "/apply", ("POST",)),
    )

    def origin(host):
        headers = {"Host": host, "Authorization": "Bearer " + "A" * 43}
        extensions = {"sni_hostname": host} if production else {}
        return {"headers": headers, "extensions": extensions}

    methods = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
    with httpx.Client(base_url=f"{scheme}://127.0.0.1:{port}", verify=False, timeout=3) as http:
        for path, supported in routes:
            before = len(received)
            for method in supported:
                allowed = http.request(method, path, **origin("admin.localhost"))
                assert allowed.status_code == 401, (method, path)
                assert allowed.headers["cache-control"] == "no-store"
            assert len(received) == before + len(supported)
            before = len(received)
            for method in methods:
                if method not in supported:
                    denied = http.request(method, path, **origin("admin.localhost"))
                    assert denied.status_code == 403, (method, path)
                    assert "access-control-allow-origin" not in denied.headers
                denied = http.request(method, path, **origin("vpn.localhost"))
                assert denied.status_code == 404, (method, path)
            assert len(received) == before
        before = len(received)
        for path in (
            collection + "/",
            collection + "/invalid",
            collection + "/" + "-" * 36,
            collection + "/" + "1" * 36,
            detail + "/extra",
            detail + "/apply/extra",
            detail + "/rollback",
            f"/api/servers/{identity}/config",
            "/api/servers/invalid/config-revisions",
            "/api/servers/" + "-" * 36 + "/config-revisions",
            "/api/servers/" + "1" * 36 + "/config-revisions",
        ):
            denied = http.post(path, **origin("admin.localhost"))
            assert denied.status_code == 404, path
        assert len(received) == before


@pytest.mark.parametrize("edge", ["development", "production"], indirect=True)
async def test_server_mutation_rbac_through_real_edge_and_api(edge, request, auth, key):  # noqa: F811
    port, received = edge
    production = request.node.callspec.params["edge"] == "production"
    scheme = "https" if production else "http"
    headers = {"Host": "admin.localhost"}
    extensions = {"sni_hostname": "admin.localhost"} if production else {}
    app = create_app()
    app.state.auth = auth
    app.state.servers = ServerService(auth.engine, key)
    loop = asyncio.get_running_loop()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://admin.localhost"
    ) as api:

        def dispatch(method, path, forwarded_headers, body):
            return asyncio.run_coroutine_threadsafe(
                api.request(method, path, headers=forwarded_headers, content=body), loop
            ).result(timeout=10)

        received.backend = dispatch
        async with httpx.AsyncClient(
            base_url=f"{scheme}://127.0.0.1:{port}", verify=False, timeout=15
        ) as http:
            tokens = {}
            for role in ("admin", "viewer"):
                _, credentials, _ = await account(auth, role)
                login = await http.post(
                    "/api/auth/login", json=credentials, headers=headers, extensions=extensions
                )
                assert login.status_code == 200, login.text
                tokens[role] = "Bearer " + login.json()["access_token"]
            created = await http.post(
                "/api/servers",
                json=payload(),
                headers=headers | {"Authorization": tokens["admin"]},
                extensions=extensions,
            )
            assert created.status_code == 201, created.text
            identity = created.json()["id"]
            for operation in ("deploy", "update", "restart", "uninstall"):
                path = f"/api/servers/{identity}/{operation}"
                body = {"idempotency_key": "edge-rbac"}
                if operation in ("deploy", "update"):
                    body["version"] = "1.2.3"
                before = len(received)
                admin_status = 202 if operation == "uninstall" else 409
                for role, expected in ((None, 401), ("viewer", 403), ("admin", admin_status)):
                    response = await http.post(
                        path,
                        json=body,
                        headers=headers | ({"Authorization": tokens[role]} if role else {}),
                        extensions=extensions,
                    )
                    assert response.status_code == expected, (operation, role, response.text)
                    assert "x-request-id" in response.headers
                    assert set(response.headers["cache-control"].split(", ")) == {"no-store"}
                assert len(received) == before + 3
                before = len(received)
                client_headers = {"Host": "vpn.localhost", "Authorization": tokens["admin"]}
                client_extensions = {"sni_hostname": "vpn.localhost"} if production else {}
                response = await http.post(
                    path, json=body, headers=client_headers, extensions=client_extensions
                )
                assert response.status_code == 404
                assert "x-request-id" not in response.headers
                assert len(received) == before
            collection = f"/api/servers/{identity}/config-revisions"
            revision_body = {
                "idempotency_key": "edge-revision",
                "config": {"udp_connections_timeout_secs": 900},
            }
            revision = None
            for role, expected in ((None, 401), ("viewer", 403), ("admin", 201)):
                before = len(received)
                response = await http.post(
                    collection,
                    json=revision_body,
                    headers=headers | ({"Authorization": tokens[role]} if role else {}),
                    extensions=extensions,
                )
                assert response.status_code == expected, (role, response.text)
                assert "x-request-id" in response.headers
                assert len(received) == before + 1
                if role == "admin":
                    revision = response.json()["id"]
            detail = collection + "/" + revision
            for path in (collection, detail):
                for role, expected in ((None, 401), ("viewer", 200), ("admin", 200)):
                    before = len(received)
                    response = await http.get(
                        path,
                        headers=headers | ({"Authorization": tokens[role]} if role else {}),
                        extensions=extensions,
                    )
                    assert response.status_code == expected, (path, role, response.text)
                    assert "x-request-id" in response.headers
                    assert len(received) == before + 1
            for role, expected in ((None, 401), ("viewer", 403), ("admin", 409)):
                before = len(received)
                response = await http.post(
                    detail + "/apply",
                    json={"idempotency_key": "edge-apply"},
                    headers=headers | ({"Authorization": tokens[role]} if role else {}),
                    extensions=extensions,
                )
                assert response.status_code == expected, (role, response.text)
                assert "x-request-id" in response.headers
                assert len(received) == before + 1
            before = len(received)
            for method, path, body in (
                ("GET", collection, None),
                ("POST", collection, revision_body),
                ("GET", detail, None),
                ("POST", detail + "/apply", {"idempotency_key": "edge-client-apply"}),
            ):
                response = await http.request(
                    method,
                    path,
                    json=body,
                    headers=client_headers,
                    extensions=client_extensions,
                )
                assert response.status_code == 404
                assert "x-request-id" not in response.headers
            assert len(received) == before
        received.backend = None
