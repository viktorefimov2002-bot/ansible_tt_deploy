"""Read-only remote checks. Never return raw commands, paths or exceptions."""

import errno
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path

from ansible.module_utils.basic import AnsibleModule


def socket_table_available(port, kind, family):
    # Management users normally cannot bind privileged ports. Linux's public
    # socket table still permits checking occupancy without acquiring sudo.
    table = "tcp" if kind == socket.SOCK_STREAM else "udp"
    table += "6" if family == socket.AF_INET6 else ""
    try:
        rows = Path("/proc/net", table).read_text().splitlines()[1:]
        return (
            "fail" if any(int(row.split()[1].split(":")[1], 16) == port for row in rows) else "pass"
        )
    except (OSError, ValueError, IndexError):
        return "unknown"


def probe_ports(port, kind):
    states = []
    for family, address in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            with socket.socket(family, kind) as sock:
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((address, port))
            states.append("pass")
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                states.append("fail")
            elif exc.errno in (errno.EACCES, errno.EPERM):
                states.append(socket_table_available(port, kind, family))
            elif exc.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                states.append("unknown")
    return (
        "fail" if "fail" in states else "unknown" if not states or "unknown" in states else "pass"
    )


def collect(acme_http):
    checks = {"ssh": "pass"}
    for name, port, kind in (
        ("tcp_80", 80, socket.SOCK_STREAM),
        ("tcp_443", 443, socket.SOCK_STREAM),
        ("udp_443", 443, socket.SOCK_DGRAM),
    ):
        checks[name] = "skipped" if port == 80 and not acme_http else probe_ports(port, kind)
    checks["architecture"] = "pass" if platform.machine() in ("x86_64", "aarch64") else "fail"
    try:
        release = platform.freedesktop_os_release()
        checks["os"] = (
            "pass"
            if (release.get("ID"), release.get("VERSION_ID"))
            in {("ubuntu", "22.04"), ("ubuntu", "24.04"), ("debian", "12"), ("debian", "13")}
            else "fail"
        )
    except OSError:
        checks["os"] = "unknown"
    try:
        checks["disk"] = "pass" if shutil.disk_usage("/").free >= 1024**3 else "fail"
    except OSError:
        checks["disk"] = "unknown"
    try:
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        checks["memory"] = "pass" if memory >= 512 * 1024**2 else "fail"
    except (OSError, ValueError):
        checks["memory"] = "unknown"
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        checks["time_sync"] = (
            {b"yes": "pass", b"no": "fail"}.get(result.stdout.strip(), "unknown")
            if result.returncode == 0
            else "unknown"
        )
    except (OSError, subprocess.TimeoutExpired):
        checks["time_sync"] = "unknown"
    return checks


def main():
    module = AnsibleModule(
        argument_spec={"acme_http": {"type": "bool", "required": True}}, supports_check_mode=True
    )
    module.exit_json(changed=False, ttcp_checks=collect(module.params["acme_http"]))


if __name__ == "__main__":
    main()
