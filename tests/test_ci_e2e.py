"""The release fixture rejects real targets and never invokes an execution adapter."""

import secrets

import pytest

from apps.execution.ports import CredentialParameters, ExecutionRequest, Outcome, Target
from scripts.ci.e2e_worker import DisposableCredentialPort


def request():
    return ExecutionRequest(
        operation="credential.create",
        target=Target(
            host="managed-node.invalid",
            user="ci_manager",
            host_key="ssh-ed25519 AAAA",
            private_key="disposable-test-only",
        ),
        credential=CredentialParameters(
            username="ttcp_" + "a" * 32,
            password=secrets.token_urlsafe(32),
            public_address="vpn-node.invalid",
        ),
    )


async def test_disposable_port_delivers_only_synthetic_configuration():
    body = request()
    result = await DisposableCredentialPort().execute(body, None)
    assert result.outcome == Outcome.SUCCEEDED
    assert 'endpoint = "vpn-node.invalid"' in result.configuration["toml"]
    assert body.credential.username in result.configuration["toml"]
    assert result.configuration["deep_link"].startswith("tt://")
    revoked = body.model_copy(
        update={
            "operation": "credential.revoke",
            "credential": body.credential.model_copy(update={"password": None}),
        }
    )
    result = await DisposableCredentialPort().execute(revoked, None)
    assert result.outcome == Outcome.SUCCEEDED
    assert result.configuration is None


@pytest.mark.parametrize("change", ["host", "user", "address", "operation"])
async def test_disposable_port_rejects_targets_outside_fixture(change):
    body = request()
    if change == "host":
        body = body.model_copy(
            update={"target": body.target.model_copy(update={"host": "unexpected.invalid"})}
        )
    elif change == "user":
        body = body.model_copy(
            update={"target": body.target.model_copy(update={"user": "unexpected"})}
        )
    elif change == "address":
        body = body.model_copy(
            update={
                "credential": body.credential.model_copy(
                    update={"public_address": "unexpected.invalid"}
                )
            }
        )
    else:
        body = body.model_copy(update={"operation": "server.status"})
    result = await DisposableCredentialPort().execute(body, None)
    assert result.outcome == Outcome.INVALID
    assert result.configuration is None
