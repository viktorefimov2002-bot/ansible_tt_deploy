"""Container probe: API HTTP readiness or worker dependency connectivity."""

import asyncio
import sys
from urllib.request import urlopen

from apps.shared.config import load_settings
from apps.shared.dependencies import connected_dependencies


async def worker_probe(settings):
    async with connected_dependencies(settings):
        pass


def main():
    try:
        settings = load_settings()
        if sys.argv[1:] == ["api"]:
            with urlopen(f"http://127.0.0.1:{settings.api_port}/readyz", timeout=12) as response:
                return 0 if response.status == 200 else 1
        if sys.argv[1:] == ["worker"]:
            asyncio.run(worker_probe(settings))
            return 0
        return 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
