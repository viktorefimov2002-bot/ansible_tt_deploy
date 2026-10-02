"""Backup routes are narrow on both development and production TLS origins."""

import httpx
import pytest

from tests.test_client_edge import edge  # noqa: F401


@pytest.mark.parametrize("edge", ["development", "production"], indirect=True)
def test_backup_routes_are_admin_origin_only_and_have_no_restore(edge, request):  # noqa: F811
    port, received = edge
    production = request.node.callspec.params["edge"] == "production"
    scheme = "https" if production else "http"

    def origin(host):
        return {
            "headers": {"Host": host, "Authorization": "Bearer " + "A" * 43},
            "extensions": {"sni_hostname": host} if production else {},
        }

    with httpx.Client(base_url=f"{scheme}://127.0.0.1:{port}", verify=False, timeout=3) as http:
        before = len(received)
        assert http.get("/api/backups?limit=50", **origin("admin.localhost")).status_code == 401
        assert http.post("/api/backups/run", **origin("admin.localhost")).status_code == 401
        assert len(received) == before + 2
        before = len(received)
        for path, methods in (
            ("/api/backups", ("POST", "PUT", "DELETE", "OPTIONS")),
            ("/api/backups/run", ("GET", "HEAD", "PUT", "DELETE", "OPTIONS")),
        ):
            for method in methods:
                assert http.request(method, path, **origin("admin.localhost")).status_code == 403
            for method in ("GET", "POST", "HEAD", "PUT", "DELETE", "OPTIONS"):
                denied = http.request(method, path, **origin("vpn.localhost"))
                assert denied.status_code == 404 and denied.headers["cache-control"] == "no-store"
        for path in (
            "/api/backups/restore",
            "/api/backups/run/extra",
            "/api/backups/settings",
            "/api/backups/buckets",
            "/api/backups/command",
            "/api/backups/",
        ):
            for method in ("GET", "POST"):
                assert http.request(method, path, **origin("admin.localhost")).status_code == 404
        assert len(received) == before
