"""Parse every deployable Compose overlay with synthetic values, without starting it."""

import argparse
import base64
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def validate(model, *, production=False, backup=False, restore=False):
    services = model["services"]
    for name in ("postgres", "redis", "api", "worker"):
        assert not services[name].get("ports"), f"{name} must remain private"
    for service in services.values():
        image = service.get("image")
        if image:
            assert ":" in image and not image.endswith(":latest"), "Unversioned image"
    nginx = services["nginx"]
    if production:
        hosts = nginx["environment"]
        assert hosts["TTCP_ADMIN_HOST"] != hosts["TTCP_CLIENT_HOST"]
        assert {str(port["target"]) for port in nginx["ports"]} == {"80", "443"}
        assert any(v["target"] == "/etc/nginx/tls" and v["read_only"] for v in nginx["volumes"])
    else:
        assert all(port["host_ip"] == "127.0.0.1" for port in nginx["ports"])
        config = (ROOT / "infra/nginx/default.conf").read_text()
        assert "server_name admin.localhost;" in config
        assert "server_name vpn.localhost;" in config
    if backup:
        assert services["worker"]["environment"]["TTCP_BACKUP_ENABLED"] == "true"
        assert any(
            v["target"] == "/run/secrets/backup.json" and v["read_only"]
            for v in services["worker"]["volumes"]
        )
        assert services["backup-maintenance"]["read_only"]
    for name in ("api", "worker"):
        assert "TTCP_RESTORE_CONFIG_FILE" not in services[name]["environment"]
        assert not any(
            v["target"] == "/run/secrets/restore.json" for v in services[name].get("volumes", [])
        )
    if restore:
        maintenance = services["backup-maintenance"]
        assert maintenance["environment"]["TTCP_RESTORE_CONFIG_FILE"] == "/run/secrets/restore.json"
        assert "tools" in maintenance["profiles"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-executable", help="Standalone Compose executable (local QA)")
    args = parser.parse_args()
    command = [args.compose_executable] if args.compose_executable else ["docker", "compose"]
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("TTCP_", "POSTGRES_", "REDIS_", "GRAFANA_", "DOCKER_"))
    }
    env["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    (ROOT / ".tools").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ci-compose-", dir=ROOT / ".tools") as directory:
        work = Path(directory)
        for filename in ("backup.json", "restore.json"):
            (work / filename).write_text("{}")
            (work / filename).chmod(0o600)
        tls = work / "tls"
        tls.mkdir()
        token = secrets.token_hex(16)
        env_file = work / "runtime.env"
        env_file.write_text(
            f"POSTGRES_PASSWORD={secrets.token_hex(32)}\n"
            f"REDIS_PASSWORD={secrets.token_hex(32)}\n"
            f"GRAFANA_ADMIN_PASSWORD={secrets.token_hex(32)}\n"
            f"TTCP_AUTH_ENCRYPTION_KEY={base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}\n"
            f"POSTGRES_DB=ttcp_ci_{token}\nHTTP_PORT=0\nGRAFANA_PORT=0\n"
            "TTCP_ADMIN_HOST=admin.release.invalid\nTTCP_CLIENT_HOST=vpn.release.invalid\n"
            f"TTCP_TLS_DIR={tls.as_posix()}\n"
            f"TTCP_BACKUP_CONFIG_PATH={(work / 'backup.json').as_posix()}\n"
            f"TTCP_RESTORE_CONFIG_PATH={(work / 'restore.json').as_posix()}\n"
        )
        env_file.chmod(0o600)
        for production, backup, restore in (
            (False, False, False),
            (True, False, False),
            (False, True, False),
            (True, True, False),
            (True, True, True),
        ):
            files = ["compose.yaml"]
            if production:
                files.append("compose.production.yaml")
            if backup:
                files.append("compose.backup.yaml")
            if restore:
                files.append("compose.restore.yaml")
            options = ["--project-name", "ttcp-ci-config-" + token, "--env-file", str(env_file)]
            for filename in files:
                options.extend(["-f", str(ROOT / "infra/compose" / filename)])
            result = subprocess.run(
                [*command, *options, "--profile", "tools", "config", "--format", "json"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise SystemExit("Compose configuration failed: " + ", ".join(files))
            validate(
                json.loads(result.stdout), production=production, backup=backup, restore=restore
            )
            print("PASS: " + ", ".join(files))


if __name__ == "__main__":
    main()
