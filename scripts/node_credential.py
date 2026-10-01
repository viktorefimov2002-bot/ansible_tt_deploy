#!/usr/bin/python3
"""Root-only, no-argument credential helper installed by managed-node bootstrap.

Accepts a bounded JSON request on stdin. Generated configurations are returned
only to the no_log execution channel.
"""

import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

DIRECTORY = Path("/opt/trusttunnel")
CREDENTIALS = DIRECTORY / "credentials.toml"
LOCK = Path("/run/lock/ttcp-credential.lock")
USERNAME = re.compile(r"ttcp_[a-f0-9]{32}\Z")
ENTRY = re.compile(
    r"\s*\[\[client\]\]\s*\nusername\s*=\s*(\"(?:[^\"\\]|\\.)*\")\s*\n"
    r"password\s*=\s*(\"(?:[^\"\\]|\\.)*\")\s*(?=\[\[client\]\]|\Z)"
)


class Rejected(Exception):
    pass


def parse(raw):
    entries = {}
    position = 0
    while position < len(raw):
        match = ENTRY.match(raw, position)
        if match is None:
            if raw[position:].strip():
                raise Rejected
            break
        username, password = (json.loads(group) for group in match.groups())
        if not isinstance(username, str) or not isinstance(password, str) or username in entries:
            raise Rejected
        entries[username] = password
        position = match.end()
    return entries


def render(entries):
    return "".join(
        "[[client]]\nusername = "
        + json.dumps(name)
        + "\npassword = "
        + json.dumps(password)
        + "\n\n"
        for name, password in entries.items()
    )


def write_atomic(content):
    fd, temporary = tempfile.mkstemp(prefix=".ttcp-", dir=DIRECTORY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CREDENTIALS)
    finally:
        Path(temporary).unlink(missing_ok=True)


def service(*args):
    result = subprocess.run(
        ["/usr/bin/systemctl", *args, "trusttunnel.service"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        check=False,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )
    if result.returncode:
        raise Rejected


def generate_configuration(username, address):
    # Same official endpoint CLI used by roles/trusttunnel_endpoint/tasks/client_configs.yml.
    result = {}
    for fmt, key in (("toml", "toml"), ("deeplink", "deep_link")):
        generated = subprocess.run(
            [
                str(DIRECTORY / "trusttunnel_endpoint"),
                "vpn.toml",
                "hosts.toml",
                "-c",
                username,
                "-a",
                address,
                "--format",
                fmt,
                "--name",
                "TrustTunnel",
            ],
            cwd=DIRECTORY,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
        if generated.returncode or not 0 < len(generated.stdout) <= 24000:
            raise Rejected
        result[key] = generated.stdout.decode("utf-8").strip()
    if not result["deep_link"].startswith("tt://"):
        raise Rejected
    return result


def main():
    if len(sys.argv) != 1 or os.geteuid() != 0:
        raise Rejected
    request = json.loads(sys.stdin.read(1025))
    if not isinstance(request, dict) or set(request) not in (
        {"operation", "username", "password"},
        {"operation", "username", "password", "public_address"},
    ):
        raise Rejected
    operation, username, password = (request[x] for x in ("operation", "username", "password"))
    if (
        operation not in {"credential.create", "credential.revoke"}
        or not isinstance(username, str)
        or not USERNAME.fullmatch(username)
    ):
        raise Rejected
    address = request.get("public_address")
    if address is not None and (
        operation != "credential.create"
        or not isinstance(address, str)
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}", address)
    ):
        raise Rejected
    if operation == "credential.create":
        if not isinstance(password, str) or not 32 <= len(password) <= 128:
            raise Rejected
    elif password is not None:
        raise Rejected
    if (
        DIRECTORY.is_symlink()
        or not DIRECTORY.is_dir()
        or DIRECTORY.stat().st_uid != 0
        or DIRECTORY.stat().st_mode & 0o022
    ):
        raise Rejected
    if CREDENTIALS.is_symlink() or not CREDENTIALS.is_file():
        raise Rejected
    info = CREDENTIALS.stat()
    if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise Rejected
    lock_fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        lock_info = os.fstat(lock.fileno())
        if (
            lock_info.st_uid != 0
            or not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_mode & 0o022
        ):
            raise Rejected
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = CREDENTIALS.read_text(encoding="utf-8")
        entries = parse(old)
        if operation == "credential.create":
            if username in entries and entries[username] != password:
                raise Rejected
            entries[username] = password
        else:
            entries.pop(username, None)
        new = render(entries)
        changed = new != old
        if changed:
            write_atomic(new)
        try:
            service("restart")
            service("is-active", "--quiet")
        except (Rejected, OSError, subprocess.TimeoutExpired):
            if changed:
                write_atomic(old)
            try:
                service("restart")
            except (Rejected, OSError, subprocess.TimeoutExpired):
                pass
            raise Rejected from None
        if operation == "credential.create" and address:
            print(json.dumps(generate_configuration(username, address)))
        if operation == "credential.revoke":
            config = DIRECTORY / ("client_" + username + ".toml")
            if config.is_file() and not config.is_symlink():
                config.unlink()


if __name__ == "__main__":
    try:
        main()
    except (
        Rejected,
        OSError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ):
        print("credential operation failed; inspect node locally", file=sys.stderr)
        raise SystemExit(1) from None
