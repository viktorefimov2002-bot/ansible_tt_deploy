"""Reviewed output projection: never inspect result data, host names or task names."""

from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "ttcp_safe"

    def v2_runner_on_ok(self, result):
        self._display.display("execution_task_ok")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._display.display("execution_task_failed")

    def v2_runner_on_unreachable(self, result):
        self._display.display("execution_unreachable")

    def v2_runner_on_skipped(self, result):
        self._display.display("execution_task_skipped")
