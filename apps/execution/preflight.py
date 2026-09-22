"""Controller DNS checks: bounded and projected to fixed diagnostic codes."""

import asyncio
import ipaddress
import socket


async def dns_checks(parameters):
    async def resolve(family):
        try:
            async with asyncio.timeout(5):
                answers = await asyncio.get_running_loop().getaddrinfo(
                    parameters.domain, 443, family=family, type=socket.SOCK_STREAM
                )
            addresses = {ipaddress.ip_address(a[4][0]) for a in answers}
            expected = ipaddress.ip_address(parameters.public_ip)
            expected_family = socket.AF_INET if expected.version == 4 else socket.AF_INET6
            if family != expected_family:
                return "pass" if addresses else "skipped"
            return "pass" if addresses == {expected} else "fail"
        except socket.gaierror as exc:
            if exc.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)):
                version = ipaddress.ip_address(parameters.public_ip).version
                return "fail" if (family == socket.AF_INET) == (version == 4) else "skipped"
            return "unknown"
        except (TimeoutError, OSError, ValueError):
            return "unknown"

    a, aaaa = await asyncio.gather(resolve(socket.AF_INET), resolve(socket.AF_INET6))
    return {"dns_a": a, "dns_aaaa": aaaa}
