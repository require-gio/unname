"""Bearer token validation.

Implemented as middleware rather than per-route dependency: any route added in later phases is
automatically protected. A missing ``Depends(...)`` line would otherwise expose an endpoint with
document content.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

#: Preflight requests have no Authorization header; the CORS middleware handles them before any
#: content is processed.
EXEMPT_METHODS = frozenset({"OPTIONS"})


class BearerTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:  # noqa: ANN001 — Starlette signature
        super().__init__(app)
        self._token = token

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in EXEMPT_METHODS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, presented = header.partition(" ")

        if scheme.lower() != "bearer" or not presented:
            return _unauthorized("Bearer-Token fehlt.")

        # Constant-time comparison to avoid leaking how many characters match.
        if not secrets.compare_digest(presented, self._token):
            return _unauthorized("Bearer-Token ungueltig.")

        return await call_next(request)


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )
