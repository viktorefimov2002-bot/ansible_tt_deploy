import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.execution.ansible import ROOT, AnsibleExecutionAdapter
from apps.execution.ports import ConfigApplyParameters, ExecutionRequest, Outcome, Target
from apps.servers.configuration import ServerConfiguration


def request():
    return ExecutionRequest(
        operation="server.config.apply",
        target=Target(
            host="192.0.2.8",
            user="ttcp",
            host_key="ssh-ed25519 AAAATEST",
            private_key="fixture-key",
        ),
        config_apply=ConfigApplyParameters(revision_id=str(uuid4()), config=ServerConfiguration()),
        timeout_seconds=600,
    )


@pytest.mark.parametrize("state", ["applied", "rolled_back", "rollback_failed"])
async def test_fixed_apply_playbook_and_safe_result_projection(state):
    selected = request()
    events = []
    temporary = []
    report = {
        "revision_id": selected.config_apply.revision_id,
        "state": state,
        "active": state != "rollback_failed",
    }

    async def runner(argv, cwd, env, output, deadline):
        temporary.append(cwd)
        assert argv[-1] == str(ROOT / "automation/ansible/managed-lifecycle.yml")
        assert deadline == 600
        parameters = json.loads((cwd / "parameters.json").read_text())
        assert parameters["ttcp_lifecycle_payload"] == {
            "operation": "server.config.apply",
            **selected.config_apply.model_dump(),
        }
        assert "fixture-key" not in str(parameters) + str(argv) + str(env)
        assert env["ANSIBLE_STDOUT_CALLBACK"] == "ttcp_safe"
        (cwd / "lifecycle.json").write_text(json.dumps(report))
        await output(b"execution_task_ok\nraw fixture-key stdout\n", False)
        return 0

    async def emit(event):
        events.append(event)

    result = await AnsibleExecutionAdapter(runner=runner).execute(selected, emit)
    assert result.outcome == Outcome.SUCCEEDED  # Job handler translates the closed apply state.
    assert (
        result.config_apply == report and result.configuration is None and result.lifecycle is None
    )
    assert "fixture-key" not in str(events) + repr(result)
    assert not temporary[0].exists()


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"revision_id": str(uuid4())},
        {"state": "healthy"},
        {"active": "true"},
        {"active": 1},
        {"active": False},
        {"raw": "fixture-secret"},
    ],
)
async def test_zero_exit_cannot_claim_missing_or_invalid_apply_receipt(change):
    selected = request()

    async def runner(argv, cwd, env, output, deadline):
        if change is not None:
            (cwd / "lifecycle.json").write_text(
                json.dumps(
                    {
                        "revision_id": selected.config_apply.revision_id,
                        "state": "applied",
                        "active": True,
                        **change,
                    }
                )
            )
        return 0

    async def emit(_):
        pass

    result = await AnsibleExecutionAdapter(runner=runner).execute(selected, emit)
    assert result.outcome == Outcome.INVALID and result.config_apply is None


@pytest.mark.parametrize(
    "changes",
    [
        {"config_apply": None},
        {"lifecycle": {"version": "1.2.3"}},
        {"playbook": "malicious.yml"},
        {"command": "id"},
        {"config_apply": {"revision_id": "../../root", "config": {}}},
        {"config_apply": {"revision_id": str(uuid4()), "config": {"raw": "listen_address='evil'"}}},
        {
            "config_apply": {
                "revision_id": str(uuid4()),
                "config": {"tls_handshake_timeout_secs": True},
            }
        },
    ],
)
def test_configuration_request_is_closed_and_strict(changes):
    selected = request()
    values = {**selected.model_dump(), "target": selected.target, **changes}
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(values)


async def test_forged_configuration_is_revalidated_before_execution():
    async def forbidden(*_):
        pytest.fail("Invalid configuration reached execution")

    selected = request()
    forged = selected.model_copy(
        update={
            "config_apply": selected.config_apply.model_copy(
                update={
                    "config": selected.config_apply.config.model_copy(
                        update={"ipv6_available": "false"}
                    )
                }
            )
        }
    )
    assert (
        await AnsibleExecutionAdapter(runner=forbidden).execute(forged, forbidden)
    ).outcome == Outcome.INVALID


def test_config_parameters_cannot_be_attached_to_other_operations():
    selected = request()
    with pytest.raises(ValidationError):
        ExecutionRequest(
            operation="server.status", target=selected.target, config_apply=selected.config_apply
        )


def test_node_privilege_boundary_defaults_match_shared_configuration():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "config_contract", ROOT / "scripts/node_lifecycle.py"
    )
    node = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(node)
    assert node.default_configuration() == ServerConfiguration().model_dump()
    assert node.validate_configuration(ServerConfiguration().model_dump())
