import importlib.util
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/bootstrap_node.py"
spec = importlib.util.spec_from_file_location("node_bootstrap", SCRIPT)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest.fixture
def key():
    # Generated per test; no real key material in repository fixtures.
    return (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )


class FakeNode(bootstrap.Node):
    """Fake OS command boundary; real admission logic and filesystem writes."""

    def __init__(self, root):
        super().__init__(root)
        self.commands = []
        self.account = ""
        self.groups = "123"
        self.fail = None
        self.ssh_conflict = False
        self.modes = {}
        self.changes = 0

    def trusted(self, name):
        # Windows cannot model Unix ownership; tested separately below.
        pass

    def write(self, name, value, mode=0o644):
        same = self.path(name).exists() and self.path(name).read_text() == value
        result = super().write(name, value, mode)
        if not same or self.modes.get(name) != mode:
            self.changes += 1
        self.modes[name] = mode
        return result

    def run(self, *args, **kwargs):
        self.commands.append(args)
        if self.fail and self.fail in args:
            raise bootstrap.BootstrapError("node command failed")
        if args[:2] == ("/usr/bin/getent", "passwd"):
            return self.account
        if args[:2] == ("/usr/bin/id", "-G"):
            return self.groups
        if args[0] == "/usr/sbin/useradd":
            # stat ownership of fixtures is controller-dependent.
            home = self.path(bootstrap.HOME)
            home.mkdir(parents=True)
            self.account = f"ttcp:x:{home.stat().st_uid}:123::/var/lib/ttcp:/bin/sh"
        if args[:2] == ("/usr/sbin/sshd", "-T"):
            if self.ssh_conflict:
                return "passwordauthentication yes"
            return "usepam yes\n" + "\n".join(
                line.strip().lower().replace(bootstrap.KEY.lower(), bootstrap.KEY)
                for line in bootstrap.SSHD.splitlines()
                if line.startswith("    ")
            )
        return ""


@pytest.fixture
def node(tmp_path, monkeypatch):
    if os.name == "nt":
        # Windows readonly attributes cannot emulate Unix mode bits. Permissions
        # are asserted from requested modes; real chmod is covered on POSIX.
        monkeypatch.setattr(bootstrap.os, "chmod", lambda *args: None)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 0, raising=False)
    instance = FakeNode(tmp_path)
    instance.path("/etc").mkdir()
    instance.path("/etc/os-release").write_text('ID=debian\nVERSION_ID="12"\n')
    instance.path("/run/systemd/system").mkdir(parents=True)
    # Use non-root synthetic uid for home validation, including on Windows.
    original_stat = Path.stat

    def home_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == instance.path(bootstrap.HOME):
            return SimpleNamespace(st_uid=123, st_mode=result.st_mode)
        return result

    monkeypatch.setattr(Path, "stat", home_stat)
    return instance


def test_public_key_validation(key):
    assert bootstrap.public_key(key + "\n") == "restrict " + key + "\n"
    for invalid in (
        key + " comment",
        key + "\n" + key,
        "command=x " + key,
        "ssh-ed25519 AAAA",
        "-----BEGIN PRIVATE KEY-----",
        "x" * 4097,
    ):
        with pytest.raises(bootstrap.BootstrapError) as error:
            bootstrap.public_key(invalid)
        assert key not in str(error.value)


@pytest.mark.parametrize(
    "os_id,version", [("debian", "12"), ("debian", "13"), ("ubuntu", "22.04"), ("ubuntu", "24.04")]
)
@pytest.mark.parametrize("arch", ["x86_64", "aarch64"])
def test_supported_platforms(node, key, monkeypatch, os_id, version, arch):
    node.path("/etc/os-release").write_text(f"ID={os_id}\nVERSION_ID={version}\n")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: arch)
    assert node.validate(bootstrap.public_key(key)) is False
    assert node.changes == 0


@pytest.mark.parametrize("failure", ["os", "arch", "root", "systemd", "sshd"])
def test_admission_failure_before_mutation(node, key, monkeypatch, failure):
    if failure == "os":
        node.path("/etc/os-release").write_text("ID=debian\nVERSION_ID=11\n")
    elif failure == "arch":
        monkeypatch.setattr(bootstrap.platform, "machine", lambda: "armv7l")
    elif failure == "root":
        monkeypatch.setattr(bootstrap.os, "geteuid", lambda: 1000)
    elif failure == "systemd":
        node.path("/run/systemd/system").rmdir()
    else:
        node.fail = "-t"
    with pytest.raises(bootstrap.BootstrapError):
        node.apply(bootstrap.public_key(key))
    assert node.changes == 0
    assert not any("/usr/sbin/useradd" in command for command in node.commands)


def test_configuration_and_repeat_run(node, key):
    normalized = bootstrap.public_key(key)
    node.apply(normalized)
    snapshot = {p: node.path(p).read_bytes() for p in node.modes if node.path(p).exists()}
    node.apply(normalized)
    assert snapshot == {p: node.path(p).read_bytes() for p in snapshot}
    assert sum(c[0] == "/usr/sbin/useradd" for c in node.commands) == 1
    assert node.modes[bootstrap.KEY] == 0o644
    assert node.modes[bootstrap.SUDO] == 0o440
    assert node.modes[bootstrap.HELPER] == 0o755
    rule = node.path(bootstrap.SUDO).read_text()
    assert f'{bootstrap.HELPER} ""' in rule
    assert "NOPASSWD: ALL" not in rule and "/bin/sh" not in rule
    assert key not in repr(node.commands)
    assert "127.0.0.1:9100" in node.path(bootstrap.EXPORTER).read_text()
    commands = node.commands
    assert (
        "/usr/bin/apt-get",
        "install",
        "-y",
        "--no-install-recommends",
        *bootstrap.PACKAGES,
    ) in commands
    assert ("/usr/sbin/usermod", "--lock", "ttcp") in commands
    assert commands[-1] == (
        "/usr/sbin/runuser",
        "-u",
        "ttcp",
        "--",
        "/usr/bin/sudo",
        "-n",
        bootstrap.HELPER,
    )


@pytest.mark.parametrize("failure", ["install", "--create-home", "-cf", "restart"])
def test_partial_failure_can_be_retried(node, key, failure):
    node.fail = failure
    with pytest.raises(bootstrap.BootstrapError):
        node.apply(bootstrap.public_key(key))
    node.fail = None
    node.apply(bootstrap.public_key(key))
    assert not node.path(bootstrap.STATE + "/sudoers.check").exists()


def test_ssh_conflict_does_not_activate_broken_configuration(node, key):
    node.ssh_conflict = True
    with pytest.raises(bootstrap.BootstrapError, match="SSH Include/Match"):
        node.apply(bootstrap.public_key(key))
    assert not node.path(bootstrap.SSH).exists()
    assert not any("reload" in command for command in node.commands)
    node.ssh_conflict = False
    node.apply(bootstrap.public_key(key))


@pytest.mark.parametrize("unsafe", ["account", "key", "groups", "owner", "exporter", "orphan"])
def test_refuse_unsafe_state(node, key, unsafe):
    normalized = bootstrap.public_key(key)
    if unsafe in {"key", "groups", "owner", "orphan"}:
        node.apply(normalized)
    if unsafe == "account":
        node.account = "ttcp:x:0:0::/root:/bin/sh"
    elif unsafe == "key":
        node.path(bootstrap.KEY).write_text("different-key")
    elif unsafe == "groups":
        node.groups = "123 27"
    elif unsafe == "owner":
        node.path(bootstrap.MARKER).write_text("unknown")
    elif unsafe == "orphan":
        node.account = ""
    else:
        node.path(bootstrap.DEFAULTS).parent.mkdir(parents=True)
        node.path(bootstrap.DEFAULTS).write_text('ARGS="--web.listen-address=:9100"')
    before = node.changes
    with pytest.raises(bootstrap.BootstrapError):
        node.apply(normalized)
    assert node.changes == before


def test_command_errors_never_expose_output(monkeypatch, key):
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout=key, stderr="provider-secret"),
    )
    with pytest.raises(bootstrap.BootstrapError) as error:
        bootstrap.Node().run("test")
    assert key not in str(error.value) and "provider-secret" not in str(error.value)


@pytest.mark.parametrize("uid,mode", [(1000, 0o755), (0, 0o777), (0, 0o775)])
def test_reject_untrusted_paths(tmp_path, monkeypatch, uid, mode):
    monkeypatch.setattr(Path, "is_symlink", lambda p: False)
    monkeypatch.setattr(Path, "exists", lambda p: True)
    monkeypatch.setattr(Path, "stat", lambda p: SimpleNamespace(st_uid=uid, st_mode=mode))
    with pytest.raises(bootstrap.BootstrapError, match="root-controlled"):
        bootstrap.Node(tmp_path).trusted(bootstrap.SUDO)


def test_reject_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "is_symlink", lambda p: True)
    with pytest.raises(bootstrap.BootstrapError, match="symlink"):
        bootstrap.Node(tmp_path).trusted(bootstrap.KEY)


@pytest.mark.skipif(os.name != "posix", reason="Unix ownership and file modes")
def test_atomic_write_is_idempotent(tmp_path):
    node = FakeNode(tmp_path)
    assert node.write("/rule", "safe", 0o440)
    assert not node.write("/rule", "safe", 0o440)
    assert stat.S_IMODE(node.path("/rule").stat().st_mode) == 0o440
    assert not list(tmp_path.glob(".ttcp-*"))


@pytest.mark.parametrize(
    "reply,code,args,expected",
    [
        ("LoadState=loaded\nActiveState=active", 0, [], 0),
        ("LoadState=not-found\nActiveState=inactive", 1, [], 0),
        ("", 1, [], 1),
        ("LoadState=loaded", 0, ["restart"], 64),
        ("LoadState=loaded", 0, ["; touch injected"], 64),
    ],
)
def test_fixed_helper_behavior(tmp_path, reply, code, args, expected):
    bash = shutil.which("bash")
    if not bash and Path("C:/Program Files/Git/bin/bash.exe").exists():
        bash = "C:/Program Files/Git/bin/bash.exe"
    if not bash:
        pytest.skip("shell unavailable")
    # Substitute only the systemctl process boundary; execute the real helper's
    # argument gate and missing-service/error handling in an actual shell.
    command = (
        "/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \\\n"
        "    /usr/bin/systemctl show trusttunnel.service --property=LoadState,ActiveState,SubState"
    )
    fake = f"printf '%s\\n' '{reply}'; exit {code}"
    assert command in bootstrap.STATUS
    helper = tmp_path / "helper.sh"
    helper.write_text(bootstrap.STATUS.replace(command, fake), newline="\n")
    result = subprocess.run([bash, str(helper), *args], capture_output=True, text=True)
    assert result.returncode == expected
    assert not (tmp_path / "injected").exists()
