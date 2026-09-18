import logging

import uvicorn

from apps.api.main import create_app
from apps.shared.config import ConfigurationError, load_settings
from apps.shared.logging import configure_logging


def main():
    configure_logging("api")
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        logging.getLogger(__name__).error(str(exc))
        return 1
    configure_logging("api", settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        timeout_graceful_shutdown=15,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
