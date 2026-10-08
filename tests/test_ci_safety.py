import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from tests.disposable import validate_ci_environment


def disposable_environment():
    suffix = secrets.token_hex(8)
    return {
        "TTCP_TEST_POSTGRES": "1",
        "TTCP_TEST_REDIS": "1",
        "TTCP_POSTGRES_HOST": "127.0.0.1",
        "TTCP_REDIS_HOST": "127.0.0.1",
        "TTCP_POSTGRES_DB": "ttcp_ci_" + suffix,
        "TTCP_POSTGRES_PASSWORD": secrets.token_hex(32),
        "TTCP_REDIS_PASSWORD": secrets.token_hex(32),
        "TTCP_TEST_REDIS_CONTAINER": "ttcp-ci-redis-" + suffix,
    }


def test_release_checks_accept_only_generated_disposable_targets():
    validate_ci_environment(disposable_environment())


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TTCP_TEST_POSTGRES", "0"),
        ("TTCP_TEST_REDIS", "0"),
        ("TTCP_POSTGRES_HOST", "production.example.test"),
        ("TTCP_REDIS_HOST", "203.0.113.1"),
        ("TTCP_POSTGRES_DB", "ttcp"),
        ("TTCP_POSTGRES_PASSWORD", "short"),
        ("TTCP_REDIS_PASSWORD", "short"),
        ("TTCP_TEST_REDIS_CONTAINER", "ttcp-dev-redis-1"),
    ],
)
def test_release_checks_refuse_external_or_uncontrolled_targets_without_echoing_secrets(
    name, value
):
    environment = disposable_environment()
    environment[name] = value
    with pytest.raises(ValueError) as failure:
        validate_ci_environment(environment)
    assert environment["TTCP_POSTGRES_PASSWORD"] not in str(failure.value)
    assert environment["TTCP_REDIS_PASSWORD"] not in str(failure.value)


@pytest.mark.parametrize("collection_skip", [False, True])
def test_skipped_release_checks_fail_the_ci_session(tmp_path, collection_skip):
    code = (
        "import pytest\npytest.skip('intentional test of CI guard', allow_module_level=True)\n"
        if collection_skip
        else "import pytest\ndef test_skip():\n    pytest.skip('intentional test of CI guard')\n"
    )
    probe = tmp_path / "test_skipped_gate.py"
    probe.write_text(code)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("TTCP_")}
    environment.update(disposable_environment(), TTCP_CI="1")
    root = Path(__file__).resolve().parents[1]
    environment["PYTHONPATH"] = str(root)
    output_path = tmp_path / "gate-output.log"
    with output_path.open("wb") as output_file:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "tests.conftest",
                "--noconftest",
                "--rootdir",
                str(tmp_path),
                "--confcutdir",
                str(tmp_path),
                "-c",
                str(root / "pyproject.toml"),
                str(probe),
            ],
            env=environment,
            cwd=tmp_path,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    output = output_path.read_bytes()
    assert process.returncode == 1, output.decode(errors="replace")
    assert b"CI forbids skipped release checks" in output
