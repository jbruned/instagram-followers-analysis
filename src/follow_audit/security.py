"""Guards for an app that has no login and listens on the local machine.

Any web page open in your browser can send requests to ``localhost``. Without these checks a page
you merely visit could read your data (DNS rebinding: a hostile name that resolves to 127.0.0.1)
or change it (a cross-site form post). So requests must name this machine in ``Host``, and
requests that change something must come from this app's own page.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}
CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';"
        " object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


def host_name(host: str) -> str:
    """The name in a Host header, without port or IPv6 brackets."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host[1:]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class LocalOnly:
    """ASGI middleware: refuse foreign Host names and cross-site changes, add safe headers."""

    def __init__(self, app: ASGIApp, allowed_hosts: set[str] | None = None) -> None:
        self.app = app
        self.allowed = LOCAL_NAMES | {h.lower() for h in (allowed_hosts or set())}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        refusal = self._refusal(scope["method"], headers)
        if refusal:
            response = PlainTextResponse(refusal, status_code=403, headers=HEADERS)
            await response(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message["headers"]}
                message["headers"] = list(message["headers"]) + [
                    (k.lower().encode(), v.encode())
                    for k, v in HEADERS.items()
                    if k.lower().encode() not in present
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _refusal(self, method: str, headers: dict[str, str]) -> str | None:
        host = headers.get("host", "")
        if host_name(host) not in self.allowed:
            return (
                f"Unknown host name '{host_name(host)}'. Open the app at http://localhost, or add"
                " the name to FA_ALLOWED_HOSTS."
            )
        if method in CHANGING:
            origin = headers.get("origin")
            if origin and urlsplit(origin).netloc.lower() != host.strip().lower():
                return "Cross-site requests are not allowed."
            if headers.get("sec-fetch-site") == "cross-site":
                return "Cross-site requests are not allowed."
        return None
