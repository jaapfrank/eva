"""Present the freshly installed core through the existing local owner session."""
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse


def register_installer_runtime_proxy(app, *, access, require_read, require_write):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    @app.api_route("/eva/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def installed_eva(request: Request, path: str):
        (require_read if request.method == "GET" else require_write)(request)
        current = access()
        if current is None:
            raise HTTPException(409, "Eva is nog niet gestart")
        base, token = current
        # The bootstrap owns the exact loopback endpoint and owner credential.
        # No browser-selected URL or auth header can change the upstream.
        parsed = urlsplit(base)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise HTTPException(503, "Ongeldige lokale installatiebinding")
        target = base + "/" + path + ("?" + request.url.query if request.url.query else "")
        headers = {"Authorization": "Bearer " + token,
                   "Accept": request.headers.get("accept", "application/json"),
                   "Content-Type": request.headers.get("content-type", "application/json")}
        from starlette.concurrency import run_in_threadpool
        data = await request.body()
        def open_upstream():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            try:
                return opener.open(urllib.request.Request(target, data=data if data else None,
                    method=request.method, headers=headers))
            except urllib.error.HTTPError as error:
                return error
        try:
            upstream = await run_in_threadpool(open_upstream)
        except (urllib.error.URLError, OSError):
            raise HTTPException(502, "De nieuwe Eva-installatie is niet bereikbaar") from None
        content_type = upstream.headers.get("content-type", "application/octet-stream")
        response_headers = {"Content-Type": content_type, "Cache-Control": "no-store",
                            "X-Content-Type-Options": "nosniff"}
        for name in ("Content-Security-Policy", "X-Eva-Input-Receipt", "X-Accel-Buffering"):
            value = upstream.headers.get(name)
            if value is not None:
                response_headers[name] = value
        if "text/html" in content_type:
            raw = await run_in_threadpool(upstream.read)
            upstream.close()
            # Existing core UI remains the UI; adapt only its absolute API paths.
            raw = raw.replace(b"/internal/v1/", b"/eva/internal/v1/")
            return Response(raw, status_code=upstream.code, headers=response_headers)
        def body():
            try:
                while chunk := upstream.read1(65536):
                    yield chunk
            finally:
                upstream.close()
        return StreamingResponse(body(), status_code=upstream.code, headers=response_headers)
