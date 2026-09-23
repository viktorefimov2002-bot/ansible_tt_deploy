"""Local, operator-run TTCP onboarding. Only an SSH public key enters via stdin."""

import base64
import os
import platform
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

USER = "ttcp"
HOME = "/var/lib/ttcp"
STATE = "/etc/ttcp-bootstrap"
MARKER = STATE + "/owner"
KEY = "/etc/ssh/ttcp_authorized_keys"
SSH = "/etc/ssh/sshd_config.d/00-ttcp.conf"
SUDO = "/etc/sudoers.d/ttcp"
HELPER = "/usr/local/sbin/ttcp-node-status"
EXPORTER = "/etc/systemd/system/prometheus-node-exporter.service.d/ttcp.conf"
DEFAULTS = "/etc/default/prometheus-node-exporter"
PACKAGES = ("python3", "sudo", "prometheus-node-exporter")
OWNER = "TTCP managed node v1\n"
STATUS = """#!/bin/sh
set -eu
[ "$#" -eq 0 ] || exit 64
state=$(/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \\
    /usr/bin/systemctl show trusttunnel.service --property=LoadState,ActiveState,SubState) || {
    case "$state" in
        *'LoadState=not-found'*) ;; # A fresh node has no VPN service yet.
        *) exit 1 ;;
    esac
}
printf '%s\\n' "$state"
"""
SUDOERS = f"""Defaults:{USER} env_reset, !setenv
{USER} ALL=(root) NOPASSWD: {HELPER} ""
"""
SSHD = f"""# Owned by TTCP bootstrap; provider access policy is unchanged.
Match User {USER}
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile {KEY}
    DisableForwarding yes
    PermitTTY no
    PermitUserRC no
Match all
"""
EXPORTER_UNIT = """[Service]
ExecStart=
ExecStart=/usr/bin/prometheus-node-exporter --web.listen-address=127.0.0.1:9100
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
"""


class BootstrapError(Exception):
    """Contains only a fixed diagnostic, never command output or supplied material."""


def public_key(value):
    # Deliberately admit one reviewed management algorithm, without key options/comments.
    if len(value) > 4096 or "\n" in value.strip() or "\r" in value.strip():
        raise BootstrapError("expected one bounded public key")
    fields = value.strip().split()
    if len(fields) != 2 or fields[0] != "ssh-ed25519":
        raise BootstrapError("expected one Ed25519 public key without options or comment")
    try:
        blob = base64.b64decode(fields[1], validate=True)
        expected = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
        if len(blob) != len(expected) + 32 or not blob.startswith(expected):
            raise ValueError
    except (ValueError, TypeError):
        raise BootstrapError("invalid public key") from None
    return "restrict " + " ".join(fields) + "\n"


class Node:
    def __init__(self, root=Path("/")):
        self.root = root

    def path(self, name):
        return self.root / name.lstrip("/")

    def run(self, *args, input=None, allowed=(0,)):
        try:
            result = subprocess.run(
                args,
                input=input,
                text=True,
                capture_output=True,
                timeout=300,
                env={
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "LC_ALL": "C",
                    "DEBIAN_FRONTEND": "noninteractive",
                },
            )
        except (OSError, subprocess.TimeoutExpired):
            raise BootstrapError(
                "required command unavailable or timed out; rerun after repair"
            ) from None
        if result.returncode not in allowed:
            raise BootstrapError("node command failed; inspect node locally and rerun")
        return result.stdout

    def trusted(self, name):
        path = self.path(name)
        for candidate in [path, *path.parents]:
            if candidate == self.root.parent:
                break
            if candidate.is_symlink():
                raise BootstrapError("symlink in managed path")
            if candidate.exists():
                mode = candidate.stat()
                if mode.st_uid != 0 or mode.st_mode & 0o022:
                    raise BootstrapError("managed path is not root-controlled")

    def write(self, name, value, mode=0o644):
        self.trusted(name)
        path = self.path(name)
        if path.exists() and not path.is_file():
            raise BootstrapError("managed path is not a regular file")
        if (
            path.exists()
            and path.read_text() == value
            and stat.S_IMODE(path.stat().st_mode) == mode
        ):
            return False
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        fd, temporary = tempfile.mkstemp(prefix=".ttcp-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                os.chmod(temporary, mode)
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return True

    def validate(self, key):
        if platform.system() != "Linux" or os.geteuid() != 0:
            raise BootstrapError("run locally as root on supported Linux")
        release = {}
        for line in self.path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                name, value = line.split("=", 1)
                release[name] = value.strip("\"'")
        if (release.get("ID"), release.get("VERSION_ID")) not in {
            ("ubuntu", "22.04"),
            ("ubuntu", "24.04"),
            ("debian", "12"),
            ("debian", "13"),
        } or platform.machine() not in {"x86_64", "aarch64"}:
            raise BootstrapError("unsupported OS or CPU architecture")
        if not self.path("/run/systemd/system").is_dir():
            raise BootstrapError("systemd required")
        self.trusted("/var/lib")
        self.run("/usr/sbin/sshd", "-t")
        for name in (MARKER, KEY, SSH, SUDO, HELPER, EXPORTER, DEFAULTS):
            self.trusted(name)
            if self.path(name).exists() and not self.path(name).is_file():
                raise BootstrapError("managed path is not a regular file")
        marker = self.path(MARKER)
        owned = marker.is_file() and marker.read_text() == OWNER
        if marker.exists() and not owned:
            raise BootstrapError("unknown bootstrap owner")
        account = self.run("/usr/bin/getent", "passwd", USER, allowed=(0, 2))
        if not account and (self.path(HOME).exists() or self.path(HOME).is_symlink()):
            raise BootstrapError("orphaned management home requires operator repair")
        if not owned:
            if (
                account
                or self.path(HOME).exists()
                or any(self.path(p).exists() for p in (KEY, SSH, SUDO, HELPER, EXPORTER))
            ):
                raise BootstrapError("refusing to adopt existing account or configuration")
            if self.path(DEFAULTS).exists():
                raise BootstrapError("existing exporter configuration requires operator review")
        if account:
            fields = account.strip().split(":")
            if len(fields) != 7 or int(fields[2]) == 0 or fields[5:] != [HOME, "/bin/sh"]:
                raise BootstrapError("unsafe management account")
            groups = self.run("/usr/bin/id", "-G", USER).split()
            if groups != [fields[3]] or fields[3] == "0":
                raise BootstrapError("management account has unexpected groups")
            home = self.path(HOME)
            if home.is_symlink() or not home.is_dir() or home.stat().st_uid != int(fields[2]):
                raise BootstrapError("unsafe management home")
        if self.path(KEY).exists() and self.path(KEY).read_text() != key:
            raise BootstrapError("management key differs; explicit rotation required")
        return bool(account)

    def apply(self, key):
        exists = self.validate(key)  # All admission checks precede mutation.
        self.write(MARKER, OWNER, 0o600)  # Allows recovery after a partial user creation.
        if not exists:
            self.run(
                "/usr/sbin/useradd",
                "--system",
                "--user-group",
                "--create-home",
                "--home-dir",
                HOME,
                "--shell",
                "/bin/sh",
                USER,
            )
        self.run("/usr/sbin/usermod", "--lock", USER)
        self.run("/usr/bin/chmod", "0700", HOME)
        self.write(KEY, key, 0o644)  # sshd opens authorized_keys as the target user.
        # Configure loopback before package postinst can start the exporter.
        self.write(DEFAULTS, 'ARGS="--web.listen-address=127.0.0.1:9100"\n')
        self.write(EXPORTER, EXPORTER_UNIT)
        self.run("/usr/bin/systemctl", "daemon-reload")
        self.run("/usr/bin/apt-get", "update")
        self.run("/usr/bin/apt-get", "install", "-y", "--no-install-recommends", *PACKAGES)
        self.write(HELPER, STATUS, 0o755)
        # Validate the exact prospective rule before installing it.
        self.write(STATE + "/sudoers.check", SUDOERS, 0o600)
        try:
            self.run("/usr/sbin/visudo", "-cf", STATE + "/sudoers.check")
        finally:
            self.path(STATE + "/sudoers.check").unlink(missing_ok=True)
        self.write(SUDO, SUDOERS, 0o440)
        self.run("/usr/sbin/visudo", "-c")
        previous = self.path(SSH).read_text() if self.path(SSH).exists() else None
        required = {
            "authenticationmethods publickey",
            "pubkeyauthentication yes",
            "passwordauthentication no",
            "kbdinteractiveauthentication no",
            f"authorizedkeysfile {KEY}",
            "disableforwarding yes",
            "permittty no",
            "permituserrc no",
            "usepam yes",  # Locked Unix password must not disable public-key login.
        }
        try:
            self.write(SSH, SSHD)
            self.run("/usr/sbin/sshd", "-t")
            effective = self.run(
                "/usr/sbin/sshd", "-T", "-C", f"user={USER},host=localhost,addr=127.0.0.1"
            )
            if not required.issubset(set(effective.splitlines())):
                raise BootstrapError("SSH Include/Match policy conflicts; repair before retry")
        except BootstrapError:
            if previous is None:
                self.path(SSH).unlink(missing_ok=True)
            else:
                self.write(SSH, previous)
            raise
        self.run("/usr/bin/systemctl", "reload", "ssh.service")
        self.run("/usr/bin/systemctl", "enable", "prometheus-node-exporter.service")
        self.run("/usr/bin/systemctl", "restart", "prometheus-node-exporter.service")
        self.run("/usr/bin/systemctl", "is-active", "--quiet", "prometheus-node-exporter.service")
        # Exercises sudo as the actual unprivileged identity, not as root.
        self.run("/usr/sbin/runuser", "-u", USER, "--", "/usr/bin/sudo", "-n", HELPER)


def main():
    try:
        if len(sys.argv) != 1:
            raise BootstrapError("no arguments accepted")
        key = public_key(sys.stdin.read(4097))
        if os.name != "posix":
            raise BootstrapError("supported Linux required")
        import fcntl

        os.umask(0o077)
        node = Node()
        node.validate(key)
        # /run is root controlled; O_NOFOLLOW also rejects a planted lock symlink.
        fd = os.open("/run/ttcp-bootstrap.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            node.apply(key)
        print("bootstrap: prepared; verify pinned SSH access before Control Plane registration")
        return 0
    except BootstrapError as error:
        print(f"bootstrap: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        # No tracebacks, subprocess output, or public/private material in terminal logs.
        print(
            "bootstrap: failed; inspect prerequisites/configuration locally, then rerun",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
