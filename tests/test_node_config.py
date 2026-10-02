"""Real render/parse/filesystem/recovery tests with Linux command boundaries simulated."""

import importlib.util
import io
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("node_config", ROOT / "scripts/node_lifecycle.py")
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)


@pytest.fixture
def configured_node(tmp_path, monkeypatch):
    for name, suffix in (
        ("DIRECTORY", "opt/trusttunnel"),
        ("UNIT", "etc/systemd/system/trusttunnel.service"),
        ("STATE", "etc/ttcp-bootstrap/workload.json"),
        ("HOOK", "etc/letsencrypt/renewal-hooks/deploy/ttcp-trusttunnel"),
        ("CONFIG_STATE", "etc/ttcp-bootstrap/config-apply"),
    ):
        monkeypatch.setattr(node, name, tmp_path / suffix)
    monkeypatch.setattr(node, "trusted", lambda _: None)  # Windows has no Unix ownership.
    monkeypatch.setattr(node.os, "fchmod", lambda *_: None, raising=False)
    if os.name != "posix":
        monkeypatch.setattr(node, "stat", SimpleNamespace(S_IMODE=lambda _: 0o644))
    monkeypatch.setattr(
        node, "TEMPLATES", ROOT / "automation/ansible/roles/trusttunnel_endpoint/templates"
    )
    node.DIRECTORY.mkdir(parents=True)
    node.atomic(node.STATE, json.dumps({"domain": "vpn.example.org"}))
    node.render("vpn.example.org")
    protected = {
        node.DIRECTORY
        / "credentials.toml": b'[[client]]\nusername="existing"\npassword="fixture"\n',
        node.DIRECTORY / "trusttunnel_endpoint": b"1.2.3",
        node.HOOK: b"fixed-renewal-hook",
        tmp_path / "etc/ssh/ttcp_authorized_keys": b"fixture-management-key",
        tmp_path / "etc/letsencrypt/live/vpn.example.org/privkey.pem": b"fixture-tls-key",
    }
    for path, content in protected.items():
        node.atomic(path, content)
    for path in (node.UNIT, node.DIRECTORY / "hosts.toml", node.DIRECTORY / "rules.toml"):
        protected[path] = path.read_bytes()
    commands = []
    monkeypatch.setattr(node, "service", lambda action: commands.append(action))
    monkeypatch.setattr(node, "version", lambda _: "1.2.3")
    monkeypatch.setattr(node, "healthy", lambda _: {"installed_version": "1.2.3", "active": True})
    request = {
        "operation": "server.config.apply",
        "revision_id": str(uuid4()),
        "config": {
            **node.default_configuration(),
            "ipv6_available": False,
            "client_listener_timeout_secs": 900,
        },
        "previous_config": node.default_configuration(),
    }
    return request, commands, protected


def assert_preserved(protected):
    assert all(path.read_bytes() == content for path, content in protected.items())


def test_apply_validated_config_and_replay_without_restart(configured_node):
    request, commands, protected = configured_node
    original = (node.DIRECTORY / "vpn.toml").read_bytes()
    receipt = node.apply(request)
    assert receipt == {"revision_id": request["revision_id"], "state": "applied", "active": True}
    parsed = node.reviewed_configuration(
        (node.DIRECTORY / "vpn.toml").read_bytes(), "vpn.example.org"
    )
    assert {name: parsed[name] for name in node.CONFIGURATION} == request["config"]
    assert commands == ["restart"]
    assert node.apply(request) == receipt
    assert commands == ["restart"]
    directory = node.CONFIG_STATE / request["revision_id"]
    assert (directory / "previous.toml").read_bytes() == original
    assert_preserved(protected)


@pytest.mark.parametrize("failure", ["partial_replace", "restart", "health", "receipt"])
def test_apply_failure_restores_previous_bytes_and_health(configured_node, monkeypatch, failure):
    request, commands, protected = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    original = vpn.read_bytes()
    durable = node.durable
    services, checks = 0, 0
    injected = False

    def write(path, content, mode=0o600):
        nonlocal injected
        durable(path, content, mode)
        if not injected and (
            (failure == "partial_replace" and path == vpn)
            or (failure == "receipt" and path.name == "receipt.json")
        ):
            injected = True
            raise OSError("fixture failure after atomic replacement")

    def service(action):
        nonlocal services
        commands.append(action)
        services += 1
        if failure == "restart" and services == 1:
            raise node.Rejected

    def healthy(_):
        nonlocal checks
        checks += 1
        if failure == "health" and checks == 2:
            raise node.Rejected
        return {"installed_version": "1.2.3", "active": True}

    monkeypatch.setattr(node, "durable", write)
    monkeypatch.setattr(node, "service", service)
    monkeypatch.setattr(node, "healthy", healthy)
    receipt = node.apply(request)
    assert receipt == {
        "revision_id": request["revision_id"],
        "state": "rolled_back",
        "active": True,
    }
    assert vpn.read_bytes() == original
    assert commands[-1] == "restart"
    assert node.apply(request) == receipt
    assert_preserved(protected)


def test_failed_rollback_is_reported_without_fake_health(configured_node, monkeypatch):
    request, _, protected = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    previous = vpn.read_bytes()
    checks = 0

    def unhealthy(_):
        nonlocal checks
        checks += 1
        if checks > 1:
            raise node.Rejected

    monkeypatch.setattr(node, "healthy", unhealthy)
    assert node.apply(request) == {
        "revision_id": request["revision_id"],
        "state": "rollback_failed",
        "active": False,
    }
    assert vpn.read_bytes() == previous  # Files restored, but runtime remains unconfirmed.
    assert node.apply(request)["state"] == "rollback_failed"
    assert_preserved(protected)


def test_partial_apply_and_restore_failure_retains_recovery_snapshot(configured_node, monkeypatch):
    request, _, protected = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    previous = vpn.read_bytes()
    durable = node.durable
    writes = 0

    def broken(path, content, mode=0o600):
        nonlocal writes
        if path == vpn:
            writes += 1
            if writes == 2:
                raise OSError("fixture restore failure")
        durable(path, content, mode)
        if path == vpn:
            raise OSError("fixture partial apply")

    monkeypatch.setattr(node, "durable", broken)
    assert node.apply(request)["state"] == "rollback_failed"
    assert vpn.read_bytes() != previous
    assert (node.CONFIG_STATE / request["revision_id"] / "previous.toml").read_bytes() == previous
    assert_preserved(protected)


def test_replay_health_failure_rolls_back_original_snapshot(configured_node, monkeypatch):
    request, commands, _ = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    previous = vpn.read_bytes()
    assert node.apply(request)["state"] == "applied"
    checks = 0

    def healthy(_):
        nonlocal checks
        checks += 1
        if checks == 1:
            raise node.Rejected
        return {"installed_version": "1.2.3", "active": True}

    monkeypatch.setattr(node, "healthy", healthy)
    assert node.apply(request)["state"] == "rolled_back"
    assert vpn.read_bytes() == previous
    assert commands == ["restart", "restart"]


@pytest.mark.parametrize("phase", ["snapshot", "journal", "candidate"])
def test_interrupted_apply_never_recaptures_candidate_as_previous(
    configured_node, monkeypatch, phase
):
    request, commands, protected = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    previous = vpn.read_bytes()
    durable = node.durable

    class ProcessKilled(BaseException):
        pass

    def killed(path, content, mode=0o600):
        durable(path, content, mode)
        if (
            (phase == "snapshot" and path.name == "previous.toml")
            or (phase == "journal" and path.name == "journal.json")
            or (phase == "candidate" and path == vpn)
        ):
            raise ProcessKilled

    monkeypatch.setattr(node, "durable", killed)
    with pytest.raises(ProcessKilled):
        node.apply(request)
    monkeypatch.setattr(node, "durable", durable)
    receipt = node.apply(request)
    if phase == "snapshot":
        assert receipt["state"] == "applied"  # No activation occurred before the journal existed.
    else:
        assert receipt["state"] == "rolled_back"
        assert vpn.read_bytes() == previous
    assert commands == ["restart"]
    assert (node.CONFIG_STATE / request["revision_id"] / "previous.toml").read_bytes() == previous
    assert_preserved(protected)


@pytest.mark.parametrize("prior_outcome", ["interrupted", "lost_applied_receipt"])
@pytest.mark.parametrize("fail_new_health", [False, True])
def test_new_revision_recovers_last_confirmed_baseline_before_snapshot(
    configured_node, monkeypatch, prior_outcome, fail_new_health
):
    request, commands, protected = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    confirmed = vpn.read_bytes()
    durable = node.durable
    if prior_outcome == "interrupted":

        class ProcessKilled(BaseException):
            pass

        def killed(path, content, mode=0o600):
            durable(path, content, mode)
            if path == vpn:
                raise ProcessKilled

        monkeypatch.setattr(node, "durable", killed)
        with pytest.raises(ProcessKilled):
            node.apply(request)
        monkeypatch.setattr(node, "durable", durable)
    else:
        assert node.apply(request)["state"] == "applied"
    assert vpn.read_bytes() != confirmed
    new_request = {
        **request,
        "revision_id": str(uuid4()),
        "config": {**request["config"], "client_listener_timeout_secs": 1000},
    }
    checks = 0

    def healthy(_):
        nonlocal checks
        checks += 1
        # Recovery confirms baseline, then the new candidate must pass its own health check.
        if fail_new_health and checks == 2:
            raise node.Rejected
        return {"installed_version": "1.2.3", "active": True}

    monkeypatch.setattr(node, "healthy", healthy)
    receipt = node.apply(new_request)
    assert receipt["state"] == ("rolled_back" if fail_new_health else "applied")
    assert (
        node.CONFIG_STATE / new_request["revision_id"] / "previous.toml"
    ).read_bytes() == confirmed
    prior_receipt = json.loads(
        (node.CONFIG_STATE / request["revision_id"] / "receipt.json").read_text()
    )
    assert prior_receipt["state"] == "rolled_back"
    if fail_new_health:
        assert vpn.read_bytes() == confirmed
    assert "restart" in commands
    assert_preserved(protected)


def test_new_revision_from_confirmed_applied_baseline_keeps_that_revision(configured_node):
    request, commands, protected = configured_node
    node.apply(request)
    confirmed = (node.DIRECTORY / "vpn.toml").read_bytes()
    new_request = {
        **request,
        "revision_id": str(uuid4()),
        "config": {**request["config"], "client_listener_timeout_secs": 1000},
        "previous_config": request["config"],
    }
    assert node.apply(new_request)["state"] == "applied"
    assert commands == ["restart", "restart"]
    assert (
        node.CONFIG_STATE / new_request["revision_id"] / "previous.toml"
    ).read_bytes() == confirmed
    assert_preserved(protected)


def test_unknown_live_configuration_without_matching_journal_fails_closed(configured_node):
    request, commands, protected = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    drift = node.render_configuration(
        "vpn.example.org", {**node.default_configuration(), "client_listener_timeout_secs": 1001}
    )
    vpn.write_bytes(drift)
    with pytest.raises(node.Rejected):
        node.apply(request)
    assert vpn.read_bytes() == drift and commands == []
    assert not node.CONFIG_STATE.exists()
    assert_preserved(protected)


def test_new_revision_repairs_restored_baseline_after_rollback_restart_failure(
    configured_node, monkeypatch
):
    request, commands, protected = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    baseline = vpn.read_bytes()
    healthy_before = node.healthy
    checks = 0

    def failed(_):
        nonlocal checks
        checks += 1
        if checks > 1:
            raise node.Rejected
        return {"installed_version": "1.2.3", "active": True}

    monkeypatch.setattr(node, "healthy", failed)
    assert node.apply(request)["state"] == "rollback_failed"
    assert vpn.read_bytes() == baseline
    restarted = False

    def repaired_service(action):
        nonlocal restarted
        commands.append(action)
        restarted = True

    def repaired_health(expected):
        if not restarted:
            raise node.Rejected  # Prerequisite is repaired, but runtime is still down.
        return healthy_before(expected)

    monkeypatch.setattr(node, "service", repaired_service)
    monkeypatch.setattr(node, "healthy", repaired_health)
    new_request = {**request, "revision_id": str(uuid4())}
    assert node.apply(new_request)["state"] == "applied"
    assert commands == ["restart", "restart", "restart", "restart"]
    assert (
        node.CONFIG_STATE / new_request["revision_id"] / "previous.toml"
    ).read_bytes() == baseline
    assert_preserved(protected)


@pytest.mark.parametrize("corruption", ["snapshot", "journal", "receipt", "drift"])
def test_corrupted_receipts_or_changed_intent_fail_closed(configured_node, corruption):
    request, commands, protected = configured_node
    node.apply(request)
    directory = node.CONFIG_STATE / request["revision_id"]
    if corruption == "snapshot":
        (directory / "previous.toml").write_bytes(b"different")
    elif corruption == "journal":
        data = json.loads((directory / "journal.json").read_text())
        data["fingerprint"] = "different"
        (directory / "journal.json").write_text(json.dumps(data))
    elif corruption == "receipt":
        (directory / "receipt.json").write_text('{"state":"applied","raw":"fixture-secret"}')
    else:
        (node.DIRECTORY / "vpn.toml").write_bytes(
            node.render_configuration("vpn.example.org", node.default_configuration())
        )
    with pytest.raises(node.Rejected):
        node.apply(request)
    assert commands == ["restart"]
    assert_preserved(protected)


def test_same_revision_different_configuration_cannot_replay(configured_node):
    request, commands, _ = configured_node
    node.apply(request)
    changed = {**request, "config": {**request["config"], "client_listener_timeout_secs": 1000}}
    with pytest.raises(node.Rejected):
        node.apply(changed)
    assert commands == ["restart"]


@pytest.mark.parametrize(
    "change",
    [
        {"config": {"raw": "credentials_file='/etc/shadow'"}},
        {"path": "/etc/shadow"},
        {"command": "id"},
        {"revision_id": "../root"},
        {"revision_id": "ABCDEFAB-CDEF-ABCD-ABCD-ABCDEFABCDEF"},
    ],
)
def test_root_config_contract_rejects_untrusted_inputs(configured_node, change):
    request, _, _ = configured_node
    with pytest.raises(node.Rejected):
        node.validate({**request, **change})


@pytest.mark.parametrize(
    "field,value",
    [
        ("ipv6_available", "false"),
        ("allow_private_network_connections", 1),
        ("tls_handshake_timeout_secs", True),
        ("tls_handshake_timeout_secs", 0),
        ("tls_handshake_timeout_secs", 121),
        ("client_listener_timeout_secs", 86401),
        ("connection_establishment_timeout_secs", 601),
        ("tcp_connections_timeout_secs", 2592001),
        ("udp_connections_timeout_secs", 86401),
    ],
)
def test_root_config_strict_types_and_bounds(configured_node, field, value):
    request, _, _ = configured_node
    with pytest.raises(node.Rejected):
        node.validate({**request, "config": {**request["config"], field: value}})


@pytest.mark.parametrize("contents", [b"invalid TOML [", b'credentials_file="/etc/shadow"\n'])
def test_malformed_or_foreign_previous_config_is_not_overwritten(configured_node, contents):
    request, commands, protected = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    vpn.write_bytes(contents)
    with pytest.raises((node.Rejected, node.tomllib.TOMLDecodeError)):
        node.apply(request)
    assert vpn.read_bytes() == contents
    assert not node.CONFIG_STATE.exists()
    assert commands == []
    assert_preserved(protected)


def test_malformed_rendered_candidate_never_reaches_production(
    configured_node, tmp_path, monkeypatch
):
    request, commands, _ = configured_node
    original = (node.DIRECTORY / "vpn.toml").read_bytes()
    templates = tmp_path / "bad-templates"
    templates.mkdir()
    (templates / "vpn.toml.j2").write_text("invalid [ TOML")
    monkeypatch.setattr(node, "TEMPLATES", templates)
    with pytest.raises(node.tomllib.TOMLDecodeError):
        node.apply(request)
    assert (node.DIRECTORY / "vpn.toml").read_bytes() == original
    assert not node.CONFIG_STATE.exists()
    assert commands == []


def test_foreign_protocol_scalar_types_are_not_accepted_as_equal(configured_node):
    request, commands, _ = configured_node
    vpn = node.DIRECTORY / "vpn.toml"
    malformed = vpn.read_text().replace(
        "disable_active_migration = true", "disable_active_migration = 1"
    )
    vpn.write_text(malformed)
    with pytest.raises(node.Rejected):
        node.apply(request)
    assert vpn.read_text() == malformed
    assert commands == []


def test_config_deadline_reserves_rollback_then_hard_stops(configured_node, tmp_path, monkeypatch):
    request, _, _ = configured_node
    monkeypatch.setattr(node, "LOCK", tmp_path / "workload.lock")
    monkeypatch.setattr(node.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(node.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(
        node.os, "fstat", lambda _: SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o600)
    )
    monkeypatch.setattr(node, "stat", stat)
    monkeypatch.setattr(node.sys, "argv", ["ttcp-node-lifecycle"])
    monkeypatch.setattr(node.sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setitem(
        node.sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *_: None)
    )
    alarms, handlers = [], []
    monkeypatch.setattr(
        node,
        "signal",
        SimpleNamespace(
            SIGALRM=14, signal=lambda _, handler: handlers.append(handler), alarm=alarms.append
        ),
    )

    def timed_out(_):
        with pytest.raises(node.Rejected):
            handlers[0]()  # The first deadline permits only the reserved rollback window.
        handlers[0]()  # Hard deadline is not caught by healthy/apply Exception handlers.

    monkeypatch.setattr(node, "apply", timed_out)
    with pytest.raises(node.DeadlineExpired):
        node.main()
    assert alarms == [420, 90]
