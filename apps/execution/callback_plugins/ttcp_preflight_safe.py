"""Project only allowlisted pre-flight check codes; discard all other result data."""

import json

from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "ttcp_preflight_safe"

    def v2_runner_on_ok(self, result):
        self._display.display("execution_task_ok")
        if getattr(getattr(result, "_task", None), "action", None) == "ttcp_preflight":
            checks = result._result.get("ttcp_checks", {})
            allowed = (
                "ssh",
                "tcp_80",
                "tcp_443",
                "udp_443",
                "os",
                "architecture",
                "disk",
                "memory",
                "time_sync",
            )
            if isinstance(checks, dict):
                safe = {
                    k: v
                    for k, v in checks.items()
                    if k in allowed
                    and isinstance(v, str)
                    and v in ("pass", "fail", "unknown", "skipped")
                }
                self._display.display("ttcp_checks:" + json.dumps(safe))

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._display.display("execution_task_failed")

    def v2_runner_on_unreachable(self, result):
        self._display.display("execution_unreachable")

    def v2_runner_on_skipped(self, result):
        self._display.display("execution_task_skipped")
