"""Exercise real Bash pipes, producer exits and secret-free smoke diagnostics."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "infra/compose/tests/smoke_assertions.sh"
BASH = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
CANARY = "synthetic-secret-must-not-appear"


def shell(script):
    env = os.environ.copy()
    env["PYTHON"] = Path(sys.executable).as_posix()
    if os.name == "nt":
        env["PATH"] = "C:/Program Files/Git/usr/bin;C:/Program Files/Git/bin;" + env["PATH"]
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=env, timeout=15)


def assertions(script):
    return shell(f"set -Eeuo pipefail; source {shlex.quote(HELPERS.as_posix())}; {script}")


def test_live_quiet_pipe_reproduces_early_close_but_capture_finishes_producer(tmp_path):
    producer = tmp_path / "producer.py"
    completed = tmp_path / "completed"
    producer.write_text(
        "import pathlib, sys, time\n"
        "print('Cache-Control: no-store', flush=True)\n"
        "time.sleep(0.05)\n"
        "sys.stdout.write('x' * 1048576)\n"
        "sys.stdout.flush()\n"
        f"pathlib.Path({completed.as_posix()!r}).write_text('finished')\n",
        encoding="utf-8",
    )
    command = f'"$PYTHON" {shlex.quote(producer.as_posix())}'
    previous = shell(f"set -o pipefail; {command} | grep -qi 'Cache-Control: no-store'")
    assert previous.returncode != 0
    assert not completed.exists()
    fixed = assertions(f"assert_output client-cache-policy 'Cache-Control: no-store' 0 {command}")
    assert fixed.returncode == 0, fixed.stderr
    assert completed.read_text() == "finished"
    assert fixed.stdout == "CHECK: client-cache-policy\n"


@pytest.mark.parametrize("code", [1, 2, 125, 255])
def test_matching_body_cannot_hide_failed_producer_or_print_secrets(code):
    result = assertions(
        f"assert_output client-root needle 0 bash -c 'printf \"needle {CANARY}\\n\"; exit {code}'"
    )
    assert result.returncode == code
    assert f"expected_exit=0 actual_exit={code}" in result.stderr
    assert CANARY not in result.stdout + result.stderr


def test_successful_command_with_wrong_response_fails_without_printing_body():
    result = assertions(f"assert_output client-root needle 0 printf '{CANARY}'")
    assert result.returncode == 1
    assert "stage=client-root assertion=response-mismatch" in result.stderr
    assert CANARY not in result.stdout + result.stderr


def test_response_matching_preserves_case_sensitive_checks_and_header_case_insensitivity():
    result = assertions("assert_output nginx-api-health ok 0 printf OK")
    assert result.returncode == 1
    headers = assertions(
        "assert_output_ci client-cache-policy 'Cache-Control: no-store' 0 "
        "printf 'cache-control: NO-STORE'"
    )
    assert headers.returncode == 0


@pytest.mark.parametrize("code", [0, 1, 255])
def test_http_denial_requires_both_response_and_expected_request_exit(code):
    result = assertions(
        "assert_output default-host-denial '404 Not Found' 1 bash -c "
        f"'printf \"404 Not Found {CANARY}\\n\"; exit {code}'"
    )
    assert result.returncode == (0 if code == 1 else code or 1)
    assert CANARY not in result.stdout + result.stderr


@pytest.mark.parametrize("code", [0, 1, 255])
def test_negative_startup_cannot_accept_compose_failure(code):
    result = assertions(f"expect_exit worker-empty-config-denial 1 bash -c 'exit {code}'")
    assert result.returncode == (0 if code == 1 else code or 1)


def test_transport_failure_is_not_retried_as_readiness():
    result = assertions(
        "checkpoint metrics-scrape; "
        f"if probe_output needle bash -c 'printf \"needle {CANARY}\\n\"; exit 255'; "
        "then echo unexpected-success; else echo unexpected-retry; fi"
    )
    assert result.returncode == 255
    assert "stage=metrics-scrape probe_exit=255" in result.stderr
    assert "unexpected-" not in result.stdout
    assert CANARY not in result.stdout + result.stderr


@pytest.mark.parametrize("command", ["printf pending", "bash -c 'exit 1'"])
def test_request_failure_or_missing_metric_remains_retryable(command):
    result = assertions(f"if probe_output needle {command}; then exit 9; else echo retry; fi")
    assert result.returncode == 0
    assert result.stdout == "retry\n"


def test_unexpected_failure_trap_logs_stage_and_status_without_command_arguments():
    result = assertions(f"checkpoint nginx-api-ready; bash -c 'echo {CANARY} >/dev/null; exit 7'")
    assert result.returncode == 7
    assert "stage=nginx-api-ready" in result.stderr
    assert "exit=7" in result.stderr
    assert CANARY not in result.stdout + result.stderr
