from __future__ import annotations

from starlette.types import Message, Receive, Scope, Send

from megaraid_dashboard.web.security_headers import SecurityHeadersMiddleware


async def test_security_headers_skip_setdefault_when_header_already_present() -> None:
    async def upstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"x-content-type-options", b"custom"),
                    (b"x-frame-options", b"custom"),
                    (b"referrer-policy", b"custom"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    middleware = SecurityHeadersMiddleware(upstream)

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        return {"type": "http.request"}

    scope: Scope = {"type": "http", "method": "GET", "path": "/"}
    await middleware(scope, receive, send)

    start_message = next(message for message in sent if message["type"] == "http.response.start")
    header_pairs = [
        (name.decode("latin-1"), value.decode("latin-1"))
        for name, value in start_message["headers"]
    ]
    for name in ("x-content-type-options", "x-frame-options", "referrer-policy"):
        matching = [value for header_name, value in header_pairs if header_name == name]
        assert matching == ["custom"], f"expected only the pre-existing {name!r} header"
