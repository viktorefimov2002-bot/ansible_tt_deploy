"""Closed, non-secret remote workload report; never accept success by exit code alone."""

import re

from apps.execution.ports import VERSION_PATTERN


def validated_result(operation, parameters, report):
    if not isinstance(report, dict) or set(report) != {"installed_version", "active"}:
        raise ValueError("Invalid lifecycle report")
    version, active = report["installed_version"], report["active"]
    if type(active) is not bool:
        raise ValueError("Invalid lifecycle report")
    if operation == "server.uninstall":
        if version is not None or active:
            raise ValueError("Uninstall not confirmed")
    elif (
        not isinstance(version, str)
        or not re.fullmatch(VERSION_PATTERN, version)
        or not active
        or (
            operation in ("server.deploy", "server.update") and version != parameters.get("version")
        )
    ):
        raise ValueError("Installed version and health not confirmed")
    return {"installed_version": version, "active": active}
