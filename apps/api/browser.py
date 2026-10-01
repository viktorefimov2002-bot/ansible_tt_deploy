"""Same-origin browser requests; no cross-origin credential policy is granted."""

from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def require_browser_request(request: Request, header: str, value: str):
    origin = request.headers.get("Origin")
    try:
        parsed = urlsplit(origin) if origin is not None else None
    except ValueError:
        raise HTTPException(403, "Same-origin browser request required") from None
    if (
        request.headers.get(header) != value
        or request.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")
        or (
            parsed is not None
            and (
                parsed.scheme not in ("http", "https")
                or parsed.netloc.lower() != request.headers.get("Host", "").lower()
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            )
        )
    ):
        raise HTTPException(403, "Same-origin browser request required")
