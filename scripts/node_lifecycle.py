#!/usr/bin/python3 -I
"""Fixed workload boundary. No argv, shell, caller paths, URLs or Ansible variables.

Reuses the endpoint role's reviewed templates and official release archive layout.
The management identity, exporter and provider recovery access are never removed.
"""

import io
import json
import os
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

DIRECTORY = Path("/opt/trusttunnel")
STATE = Path("/etc/ttcp-bootstrap/workload.json")
UNIT = Path("/etc/systemd/system/trusttunnel.service")
HOOK = Path("/etc/letsencrypt/renewal-hooks/deploy/ttcp-trusttunnel")
TEMPLATES = Path("/etc/ttcp-bootstrap/lifecycle-templates")
CERTIFICATES = Path("/etc/letsencrypt/live")
LOCK = Path("/run/lock/ttcp-workload.lock")
VERSION = r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})"
OPERATIONS = {"server.deploy", "server.update", "server.restart", "server.uninstall"}
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "HOME": "/root"}


class Rejected(Exception):
    pass


def validate(request):
    if not isinstance(request, dict) or request.get("operation") not in OPERATIONS:
        raise Rejected
    op = request["operation"]
    allowed = {"operation", "acme_http"}
    if op in {"server.deploy", "server.update"}:
        allowed.add("version")
        if not isinstance(request.get("version"), str) or not re.fullmatch(
            VERSION, request["version"]
        ):
            raise Rejected
    if op == "server.deploy":
        allowed |= {"domain", "acme_email"}
        domain = request.get("domain")
        if (
            not isinstance(domain, str)
            or len(domain) > 253
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in domain.split(".")
            )
            or domain.replace(".", "").isdigit()
        ):
            raise Rejected
        email = request.get("acme_email")
        if email is not None and (
            not isinstance(email, str)
            or len(email) > 254
            or not re.fullmatch(r"[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+", email)
        ):
            raise Rejected
        if request.get("acme_http") and not email:
            raise Rejected
    elif request.get("acme_http", False):
        raise Rejected
    if type(request.get("acme_http", False)) is not bool or set(request) - allowed:
        raise Rejected
    return request


def trusted(path):
    # Check each ancestor: an unprivileged parent could substitute a trusted file.
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise Rejected
        if candidate.exists():
            info = candidate.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise Rejected


def atomic(path, content, mode=0o600):
    trusted(path)
    if path.exists() and not path.is_file():
        raise Rejected
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temporary = tempfile.mkstemp(prefix=".ttcp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content.encode() if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def command(*argv, timeout=30, allowed=(0,)):
    result = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=ENV,
    )
    if result.returncode not in allowed or len(result.stdout) > 65536:
        raise Rejected
    return result.stdout.decode("utf-8").strip()


def service(action):
    return command("/usr/bin/systemctl", action, "trusttunnel.service")


def version(executable):
    output = command(str(executable), "--version", timeout=3)
    match = re.fullmatch(r"[^0-9\n]*(" + VERSION + r")\s*", output)
    if not match:
        raise Rejected
    return match[1]


def release_binary(release):
    architecture = platform.machine()
    if architecture not in {"x86_64", "aarch64"}:
        raise Rejected
    name = f"trusttunnel-v{release}-linux-{architecture}"
    url = f"https://github.com/TrustTunnel/TrustTunnel/releases/download/v{release}/{name}.tar.gz"
    # HTTPS uses system CA verification; no caller URL or installer script is run.
    with urllib.request.urlopen(url, timeout=30) as response:
        if not response.url.startswith("https://"):
            raise Rejected
        archive = response.read(64 * 1024**2 + 1)
    if len(archive) > 64 * 1024**2:
        raise Rejected
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        member = package.getmember(name + "/trusttunnel_endpoint")
        if not member.isfile() or not 0 < member.size <= 128 * 1024**2:
            raise Rejected
        # Read exactly one regular member. Never extract archive paths or links as root.
        with package.extractfile(member) as stream:
            return stream.read()


def render(domain):
    from jinja2 import Environment, StrictUndefined

    env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    env.filters.update(to_json=json.dumps, bool=bool)
    values = dict(
        trusttunnel_domain=domain,
        trusttunnel_install_dir=str(DIRECTORY),
        trusttunnel_listen_address="0.0.0.0:443",
        trusttunnel_ipv6_available=True,
        trusttunnel_allow_private_network_connections=False,
        trusttunnel_cert_chain_path=f"/etc/letsencrypt/live/{domain}/fullchain.pem",
        trusttunnel_private_key_path=f"/etc/letsencrypt/live/{domain}/privkey.pem",
        trusttunnel_enable_ping_host=False,
        trusttunnel_enable_speedtest_host=False,
    )
    for name, destination, mode in (
        ("vpn.toml.j2", DIRECTORY / "vpn.toml", 0o644),
        ("hosts.toml.j2", DIRECTORY / "hosts.toml", 0o644),
        ("rules.toml.j2", DIRECTORY / "rules.toml", 0o644),
        ("trusttunnel.service.j2", UNIT, 0o644),
    ):
        source = TEMPLATES / name
        trusted(source)
        atomic(destination, env.from_string(source.read_text()).render(values), mode)


def healthy(expected):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            service("is-active")
            pid = command(
                "/usr/bin/systemctl", "show", "--property=MainPID", "--value", "trusttunnel.service"
            )
            if (
                not pid.isdecimal()
                or int(pid) <= 0
                or version(Path(f"/proc/{pid}/exe")) != expected
            ):
                raise Rejected
            with socket.create_connection(("127.0.0.1", 443), timeout=1):
                pass
            udp = Path("/proc/net/udp").read_text() + Path("/proc/net/udp6").read_text()
            if not any(
                line.split()[1].endswith(":01BB")
                for line in udp.splitlines()
                if len(line.split()) > 2
            ):
                raise Rejected
            return {"installed_version": expected, "active": True}
        except (Rejected, OSError, subprocess.TimeoutExpired):
            time.sleep(1)
    raise Rejected


def apply(request):
    op = request["operation"]
    for path in (DIRECTORY, STATE, UNIT, HOOK):
        trusted(path)
    if not STATE.exists():
        if DIRECTORY.exists() or UNIT.exists() or HOOK.exists():
            raise Rejected  # Never adopt/delete a standalone or foreign installation.
        if op == "server.uninstall":
            return {"installed_version": None, "active": False}
        if op != "server.deploy":
            raise Rejected
        atomic(STATE, json.dumps({"domain": request["domain"]}))
    state = json.loads(STATE.read_text())
    domain = state["domain"]
    if op == "server.deploy" and request["domain"] != domain:
        raise Rejected  # Config changes/rollback belong to TTCP-018.
    if DIRECTORY.exists():
        if not DIRECTORY.is_dir():
            raise Rejected
        for path in DIRECTORY.rglob("*"):
            trusted(path)
    if op == "server.uninstall":
        if UNIT.exists():
            service("stop")
            service("disable")
            UNIT.unlink()
        command("/usr/bin/systemctl", "daemon-reload")
        # No SSH/firewall/packages/exporter/certificate deletion, only owned workload.
        if DIRECTORY.exists():
            shutil.rmtree(DIRECTORY)
        HOOK.unlink(missing_ok=True)
        STATE.unlink()
        return {"installed_version": None, "active": False}
    if op == "server.restart":
        expected = version(DIRECTORY / "trusttunnel_endpoint")
        service("restart")
        return healthy(expected)
    expected = request["version"]
    DIRECTORY.mkdir(exist_ok=True, mode=0o755)
    executable = DIRECTORY / "trusttunnel_endpoint"
    installed = None
    if executable.exists():
        installed = version(executable)
    if installed != expected:
        binary = release_binary(expected)  # Download completely before stopping service.
        if UNIT.exists():
            service("stop")
        atomic(executable, binary, 0o755)
        if version(executable) != expected:
            raise Rejected
    if op == "server.deploy":
        cert = CERTIFICATES / domain / "fullchain.pem"
        if request.get("acme_http"):
            command(
                "/usr/bin/certbot",
                "certonly",
                "--standalone",
                "--non-interactive",
                "--agree-tos",
                "--email",
                request["acme_email"],
                "-d",
                domain,
                "--keep-until-expiring",
                timeout=180,
            )
        if not cert.is_file() or not cert.with_name("privkey.pem").is_file():
            raise Rejected
        render(domain)
        if not (DIRECTORY / "credentials.toml").exists():
            atomic(DIRECTORY / "credentials.toml", "")  # No bootstrap VPN credentials.
        atomic(HOOK, "#!/bin/sh\n/usr/bin/systemctl try-restart trusttunnel.service\n", 0o755)
    elif not UNIT.exists() or not (DIRECTORY / "vpn.toml").is_file():
        raise Rejected  # Update does not invent or overwrite configuration.
    command("/usr/bin/systemctl", "daemon-reload")
    service("enable")
    service("start")  # Same-version replay ensures health without another restart.
    return healthy(expected)


def main():
    if len(sys.argv) != 1 or os.geteuid() != 0:
        raise Rejected
    request = validate(json.loads(sys.stdin.read(4097)))
    import fcntl

    os.umask(0o077)
    # A shared lock also serializes credential edits/restarts against lifecycle.
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        info = os.fstat(lock.fileno())
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise Rejected
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def expired(*_):
            raise Rejected

        signal.signal(signal.SIGALRM, expired)
        signal.alarm(540)  # Remote work is bounded even after an SSH/controller loss.
        print(json.dumps(apply(request)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("lifecycle operation failed; inspect node locally", file=sys.stderr)
        raise SystemExit(1) from None
