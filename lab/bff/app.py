"""
BFF — Minimal ASGI reverse proxy with httpx (weak link)
"""
import httpx

BACKEND_URL = "http://backend"

client = httpx.AsyncClient(
    base_url=BACKEND_URL,
    limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
    timeout=httpx.Timeout(30.0),
    http2=False,
)

# Hop-by-hop headers: not forwarded (RFC 7230)
HOP_BY_HOP = {
    b"host", b"connection", b"keep-alive",
    b"transfer-encoding", b"te", b"upgrade",
}

# Response headers to strip: Uvicorn recalculates them automatically
SKIP_RESP = {"transfer-encoding", "content-length"}

async def app(scope, receive, send):
    if scope["type"] != "http":
        return

    # raw_path preserves encoded %0d%0a; scope["path"] would decode them
    raw_path = scope.get("raw_path", b"/").decode("latin-1")
    query = scope.get("query_string", b"").decode("latin-1")
    path = f"{raw_path}?{query}" if query else raw_path

    # Headers: forward everything except hop-by-hop
    # Set Host: localhost so $host in Nginx doesn't become "backend"
    headers = [
        (k.decode("latin-1"), v.decode("latin-1"))
        for k, v in scope["headers"]
        if k.lower() not in HOP_BY_HOP
    ]
    headers.append(("host", "localhost"))

    # Body
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break

    try:
        resp = await client.request(scope["method"], path, headers=headers, content=body or None)

        resp_headers = [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in resp.headers.items()
            # Strip problematic headers and those containing CRLF, not really necessary but wanted to include both options
            #if k.lower() not in SKIP_RESP and "\r" not in v and "\n" not in v
            if k.lower() not in SKIP_RESP
        ]

        await send({"type": "http.response.start", "status": resp.status_code, "headers": resp_headers})
        await send({"type": "http.response.body", "body": resp.content})

    except Exception as e:
        import traceback
        traceback.print_exc()
        await send({"type": "http.response.start", "status": 500, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": f"BFF Error: {e}".encode()})