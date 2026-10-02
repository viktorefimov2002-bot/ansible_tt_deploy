#!/usr/bin/python3 -I
"""Fixed workload boundary. No argv, shell, caller paths, URLs or Ansible variables.

Reuses the endpoint role's reviewed templates and official release archive layout.
The management identity, exporter and provider recovery access are never removed.
"""

import hashlib
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
from uuid import UUID

try:
    import tomllib
except ImportError:  # Ubuntu 22.04's system Python uses bootstrap-installed Tomli.
    import tomli as tomllib

DIRECTORY = Path("/opt/trusttunnel")
STATE = Path("/etc/ttcp-bootstrap/workload.json")
UNIT = Path("/etc/systemd/system/trusttunnel.service")
HOOK = Path("/etc/letsencrypt/renewal-hooks/deploy/ttcp-trusttunnel")
TEMPLATES = Path("/etc/ttcp-bootstrap/lifecycle-templates")
CERTIFICATES = Path("/etc/letsencrypt/live")
LOCK = Path("/run/lock/ttcp-workload.lock")
CONFIG_STATE = Path("/etc/ttcp-bootstrap/config-apply")
VERSION = r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})"
OPERATIONS = {
    "server.deploy",
    "server.update",
    "server.restart",
    "server.uninstall",
    "server.config.apply",
}
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "HOME": "/root"}
CONFIGURATION = {
    "ipv6_available": (True, bool, None),
    "allow_private_network_connections": (False, bool, None),
    "tls_handshake_timeout_secs": (10, int, (1, 120)),
    "client_listener_timeout_secs": (600, int, (1, 86400)),
    "connection_establishment_timeout_secs": (30, int, (1, 600)),
    "tcp_connections_timeout_secs": (604800, int, (1, 2592000)),
    "udp_connections_timeout_secs": (300, int, (1, 86400)),
}


class Rejected(Exception):
    pass


class DeadlineExpired(BaseException):
    """Hard stop after the rollback reserve; cannot be swallowed by health retries."""


def validate(request):
    if not isinstance(request, dict) or request.get("operation") not in OPERATIONS:
        raise Rejected
    op = request["operation"]
    if op == "server.config.apply":
        if set(request) != {"operation", "revision_id", "config", "previous_config"}:
            raise Rejected
        try:
            if str(UUID(request["revision_id"])) != request["revision_id"]:
                raise Rejected
        except (ValueError, TypeError, AttributeError):
            raise Rejected from None
        validate_configuration(request["config"])
        validate_configuration(request["previous_config"])
        return request
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


def validate_configuration(config):
    # Independently enforce the closed contract at the root privilege boundary.
    if not isinstance(config, dict) or set(config) != set(CONFIGURATION):
        raise Rejected
    for name, (_, kind, bounds) in CONFIGURATION.items():
        value = config[name]
        if type(value) is not kind or (bounds and not bounds[0] <= value <= bounds[1]):
            raise Rejected
    return config


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


def sync_directory(path):
    if os.name == "posix":
        # Persist directory entries before runtime mutation or acknowledging a receipt.
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def durable(path, content, mode=0o600):
    atomic(path, content, mode)
    sync_directory(path.parent)


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


def render_environment(domain, config=None):
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
    values.update(
        {
            "trusttunnel_" + name: value
            for name, value in (config or default_configuration()).items()
        }
    )
    return env, values


def default_configuration():
    return {name: value[0] for name, value in CONFIGURATION.items()}


def render_configuration(domain, config):
    env, values = render_environment(domain, validate_configuration(config))
    source = TEMPLATES / "vpn.toml.j2"
    trusted(source)
    return env.from_string(source.read_text()).render(values).encode()


def reviewed_configuration(content, domain):
    parsed = tomllib.loads(content.decode("utf-8"))
    baseline = tomllib.loads(render_configuration(domain, default_configuration()).decode())
    if set(parsed) != set(baseline):
        raise Rejected
    validate_configuration({name: parsed[name] for name in CONFIGURATION})
    immutable = {name: parsed[name] for name in set(baseline) - set(CONFIGURATION)}
    reviewed = {name: baseline[name] for name in immutable}
    if json.dumps(immutable, sort_keys=True) != json.dumps(reviewed, sort_keys=True):
        raise Rejected
    return parsed


def render(domain):
    env, values = render_environment(domain)
    for name, destination, mode in (
        ("vpn.toml.j2", DIRECTORY / "vpn.toml", 0o644),
        ("hosts.toml.j2", DIRECTORY / "hosts.toml", 0o644),
        ("rules.toml.j2", DIRECTORY / "rules.toml", 0o644),
        ("trusttunnel.service.j2", UNIT, 0o644),
    ):
        source = TEMPLATES / name
        trusted(source)
        atomic(destination, env.from_string(source.read_text()).render(values), mode)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def config_receipt(revision, state):
    return {"revision_id": revision, "state": state, "active": state != "rollback_failed"}


def verified_journal(directory, domain):
    journal, snapshot = directory / "journal.json", directory / "previous.toml"
    for path in (directory, journal, snapshot):
        trusted(path)
    state = json.loads(journal.read_text())
    if (
        not isinstance(state, dict)
        or set(state)
        != {
            "fingerprint",
            "previous_version",
            "previous_mode",
            "previous_sha256",
            "candidate_sha256",
        }
        or any(
            not isinstance(state[key], str) or not re.fullmatch(r"[a-f0-9]{64}", state[key])
            for key in ("fingerprint", "previous_sha256", "candidate_sha256")
        )
        or not isinstance(state["previous_version"], str)
        or not re.fullmatch(VERSION, state["previous_version"])
        or type(state["previous_mode"]) is not int
        or state["previous_mode"] not in {0o600, 0o640, 0o644}
        or not snapshot.is_file()
    ):
        raise Rejected
    previous = snapshot.read_bytes()
    if digest(previous) != state["previous_sha256"]:
        raise Rejected
    reviewed_configuration(previous, domain)
    return state, previous


def verified_receipt(path, revision):
    trusted(path)
    receipt = json.loads(path.read_text())
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"revision_id", "state", "active"}
        or receipt["revision_id"] != revision
        or receipt["state"] not in ("applied", "rolled_back", "rollback_failed")
        or type(receipt["active"]) is not bool
        or receipt != config_receipt(revision, receipt["state"])
    ):
        raise Rejected
    return receipt


def confirmed_baseline(request, domain, expected):
    vpn = DIRECTORY / "vpn.toml"
    current = vpn.read_bytes()
    parsed = reviewed_configuration(current, domain)
    if {name: parsed[name] for name in CONFIGURATION} == request["previous_config"]:
        try:
            healthy(expected)
        except Exception:
            # The confirmed file may have been restored before an interrupted or
            # failed rollback restart. Repair only that exact typed baseline.
            service("restart")
            healthy(expected)
        return current
    # Recover only a reviewed prior operation matching the live candidate and the
    # Control Plane's last confirmed settings. Foreign drift has no trusted match.
    if CONFIG_STATE.is_dir():
        for directory in sorted(CONFIG_STATE.iterdir()):
            trusted(directory)
            try:
                if str(UUID(directory.name)) != directory.name or not directory.is_dir():
                    continue
            except ValueError:
                continue
            if not (directory / "journal.json").is_file():
                continue
            state, previous = verified_journal(directory, domain)
            parsed_previous = reviewed_configuration(previous, domain)
            if (
                state["candidate_sha256"] != digest(current)
                or state["previous_version"] != expected
                or {name: parsed_previous[name] for name in CONFIGURATION}
                != request["previous_config"]
            ):
                continue
            receipt_path = directory / "receipt.json"
            if (
                receipt_path.exists()
                and verified_receipt(receipt_path, directory.name)["state"] == "rolled_back"
            ):
                continue
            try:
                durable(vpn, previous, state["previous_mode"])
                service("restart")
                healthy(expected)
                durable(receipt_path, json.dumps(config_receipt(directory.name, "rolled_back")))
                return previous
            except Exception:
                durable(receipt_path, json.dumps(config_receipt(directory.name, "rollback_failed")))
                raise Rejected from None
    raise Rejected


def apply_configuration(request, domain):
    validate(request)
    vpn = DIRECTORY / "vpn.toml"
    for path in (vpn, DIRECTORY / "trusttunnel_endpoint", UNIT, CONFIG_STATE):
        trusted(path)
    if not vpn.is_file() or not UNIT.is_file():
        raise Rejected
    candidate = render_configuration(domain, request["config"])
    reviewed_configuration(candidate, domain)  # Parse and inspect before any production write.
    revision = request["revision_id"]
    fingerprint = digest(json.dumps(request, sort_keys=True, separators=(",", ":")).encode())
    directory = CONFIG_STATE / revision
    trusted(directory)
    journal = directory / "journal.json"
    snapshot = directory / "previous.toml"
    receipt_path = directory / "receipt.json"
    for path in (journal, snapshot, receipt_path):
        trusted(path)
    if journal.exists():
        state, previous = verified_journal(directory, domain)
        if state["fingerprint"] != fingerprint or state["candidate_sha256"] != digest(candidate):
            raise Rejected
        if receipt_path.exists():
            receipt = verified_receipt(receipt_path, revision)
            current = vpn.read_bytes()
            if receipt["state"] == "rollback_failed":
                return receipt
            if digest(current) != (
                state["candidate_sha256"]
                if receipt["state"] == "applied"
                else state["previous_sha256"]
            ):
                raise Rejected  # Drift or another revision cannot be misreported as this one.
            try:
                healthy(state["previous_version"])
                return receipt  # Healthy replay does not repeat the service restart.
            except Exception:
                pass  # Restore the snapshot when replay's health confirmation fails.
        # A journal without a receipt means an interrupted operation. Never recapture
        # production as the snapshot; it may already contain the failed candidate.
    else:
        if receipt_path.exists():
            raise Rejected
        expected = version(DIRECTORY / "trusttunnel_endpoint")
        previous = confirmed_baseline(request, domain, expected)
        if snapshot.exists() and snapshot.read_bytes() != previous:
            raise Rejected  # Never adopt uncertain production after a missing journal.
        mode = stat.S_IMODE(vpn.stat().st_mode)
        if mode not in {0o600, 0o640, 0o644}:
            raise Rejected
        CONFIG_STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        sync_directory(CONFIG_STATE.parent)
        directory.mkdir(exist_ok=True, mode=0o700)
        sync_directory(CONFIG_STATE)
        durable(snapshot, previous)
        state = {
            "fingerprint": fingerprint,
            "previous_version": expected,
            "previous_mode": mode,
            "previous_sha256": digest(previous),
            "candidate_sha256": digest(candidate),
        }
        durable(journal, json.dumps(state))
        try:
            durable(vpn, candidate, mode)
            service("restart")
            healthy(expected)
            receipt = config_receipt(revision, "applied")
            durable(receipt_path, json.dumps(receipt))
            return receipt
        except Exception:
            pass  # Any partial apply, restart, health or acknowledgement failure rolls back.
    try:
        durable(vpn, previous, state["previous_mode"])
        service("restart")
        healthy(state["previous_version"])
        receipt = config_receipt(revision, "rolled_back")
    except Exception:
        receipt = config_receipt(revision, "rollback_failed")
    durable(receipt_path, json.dumps(receipt))
    return receipt


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
    if op == "server.config.apply":
        return apply_configuration(request, domain)
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

        rollback_reserve = False

        def expired(*_):
            nonlocal rollback_reserve
            if request["operation"] == "server.config.apply":
                if rollback_reserve:
                    raise DeadlineExpired
                rollback_reserve = True
                signal.alarm(90)  # Total node work is bounded to 420 + 90 seconds.
            raise Rejected

        signal.signal(signal.SIGALRM, expired)
        signal.alarm(420 if request["operation"] == "server.config.apply" else 540)
        print(json.dumps(apply(request)))


if __name__ == "__main__":
    try:
        main()
    except (Exception, DeadlineExpired):
        print("lifecycle operation failed; inspect node locally", file=sys.stderr)
        raise SystemExit(1) from None
