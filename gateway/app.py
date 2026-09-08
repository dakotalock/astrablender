"""Single-workstation authentication and streaming proxy. No Blender reimplementation."""
import asyncio
import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web
from multidict import CIMultiDict
from yarl import URL

COOKIE = "workstation_session"
SESSION_SECONDS = 8 * 60 * 60
LOGIN = "/__workstation/login"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade"}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                            n=16384, r=8, p=1).hex()
    return "scrypt:" + salt + ":" + digest


def check_password(password, encoded):
    if len(password) > 1024:
        return False
    try:
        method, salt, digest = encoded.split(":")
        if method != "scrypt" or len(salt) != 32 or len(digest) != 128:
            return False
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError, UnicodeError):
        return False


@dataclass(frozen=True)
class Config:
    origin: str
    username: str
    password: str
    key: bytes
    upstream: str = "http://blender:3000"

    def __post_init__(self):
        u = urlsplit(self.origin)
        if u.scheme not in ("https", "http") or not u.netloc or u.path or u.query or u.fragment or u.username:
            raise ValueError("PUBLIC_ORIGIN must be an origin without a path")
        if u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Public access requires HTTPS")
        if len(self.key) < 32 or not self.username or not self.password.startswith("scrypt:"):
            raise ValueError("Run configure.py to generate valid authentication settings")
        upstream = urlsplit(self.upstream)
        if upstream.scheme not in ("http", "https") or not upstream.netloc or upstream.path:
            raise ValueError("UPSTREAM must be one fixed server origin")


def new_session(config, now=None):
    now = time.time() if now is None else now
    payload = base64.urlsafe_b64encode(json.dumps({
        "exp": int(now) + SESSION_SECONDS, "nonce": secrets.token_hex(16)
    }).encode()).decode().rstrip("=")
    sig = hmac.new(config.key, payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + sig


def session_expiry(config, token, now=None):
    now = time.time() if now is None else now
    try:
        payload, signature = token.split(".")
        expected = hmac.new(config.key, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        expiry = value["exp"]
        if isinstance(expiry, int) and now < expiry <= now + SESSION_SECONDS + 1:
            return expiry
    except (ValueError, TypeError, KeyError, UnicodeError):
        pass
    return None


def filtered_headers(headers, request=False):
    blocked = HOP | {v.strip().lower() for v in headers.get("Connection", "").split(",")}
    if request:
        blocked |= {"host", "cookie", "authorization", "forwarded", "x-forwarded-for",
                    "x-forwarded-host", "x-forwarded-proto", "content-length"}
    return CIMultiDict((k, v) for k, v in headers.items()
                       if k.lower() not in blocked and not k.lower().startswith("sec-websocket-"))


def create_app(config):
    app = web.Application(client_max_size=128 * 1024 * 1024,
                          handler_args={"auto_decompress": False})
    failed_logins = deque(maxlen=12)
    template = Path(__file__).with_name("login.html").read_text()
    client = None
    open_access = os.environ.get("OPEN_ACCESS", "").lower() in ("1", "true", "yes")

    async def client_context(app):
        nonlocal client
        async with ClientSession(auto_decompress=False,
                                 timeout=ClientTimeout(total=None, sock_connect=10)) as client:
            yield
    app.cleanup_ctx.append(client_context)

    def login_page(message="", status=200):
        return web.Response(text=template.replace("{{MESSAGE}}", html.escape(message)),
                            content_type="text/html", status=status, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
        })

    def origin_ok(request):
        return request.headers.get("Origin") == config.origin

    async def login(request):
        if open_access:
            raise web.HTTPSeeOther("/")
        if request.method == "GET":
            return login_page()
        if request.headers.get("Content-Encoding", "identity") != "identity":
            raise web.HTTPUnsupportedMediaType(text="Compressed login forms are not supported.")
        if not origin_ok(request):
            raise web.HTTPForbidden(text="Use this workstation's sign-in page.")
        if request.content_length is None or request.content_length > 8192:
            raise web.HTTPRequestEntityTooLarge(max_size=8192, actual_size=request.content_length or 0)
        now = time.monotonic()
        while failed_logins and now - failed_logins[0] >= 60:
            failed_logins.popleft()
        if len(failed_logins) >= 12:
            return login_page("Too many attempts. Wait a minute and try again.", 429)
        # Reserve before hashing so concurrent attempts cannot bypass the limit.
        failed_logins.append(now)
        data = await request.post()
        valid = await asyncio.to_thread(check_password, str(data.get("password", "")), config.password)
        user_ok = hmac.compare_digest(str(data.get("username", "")).encode(), config.username.encode())
        if not valid or not user_ok:
            return login_page("The username or password was not accepted.", 401)
        response = web.HTTPSeeOther("/")
        response.set_cookie(COOKIE, new_session(config), max_age=SESSION_SECONDS,
                            secure=config.origin.startswith("https:"), httponly=True,
                            samesite="Strict", path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    async def websocket(request, target, expiry):
        headers = filtered_headers(request.headers, request=True)
        headers["Origin"] = config.origin
        protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if p.strip()]
        try:
            remote = await client.ws_connect(target, headers=headers, protocols=protocols,
                                             heartbeat=30, max_msg_size=64 * 1024 * 1024)
        except (ClientError, asyncio.TimeoutError):
            raise web.HTTPBadGateway(text="The Blender desktop is not ready. Try again shortly.")
        local = web.WebSocketResponse(protocols=[remote.protocol] if remote.protocol else (),
                                      heartbeat=30, max_msg_size=64 * 1024 * 1024)
        await local.prepare(request)

        async def relay(source, dest):
            async for message in source:
                if message.type == WSMsgType.TEXT:
                    await dest.send_str(message.data)
                elif message.type == WSMsgType.BINARY:
                    await dest.send_bytes(message.data)
                elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                    break

        tasks = [asyncio.create_task(relay(local, remote)),
                 asyncio.create_task(relay(remote, local))]
        try:
            await asyncio.wait(tasks, timeout=max(0, expiry - time.time()),
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await remote.close()
            await local.close()
        return local

    async def proxy(request):
        if open_access:
            expiry = time.time() + SESSION_SECONDS
        else:
            expiry = session_expiry(config, request.cookies.get(COOKIE, ""))
            if expiry is None:
                if request.method == "GET" and request.path == "/":
                    raise web.HTTPSeeOther(LOGIN)
                raise web.HTTPUnauthorized(text="Sign in to the workstation first.")
        is_ws = request.headers.get("Upgrade", "").lower() == "websocket"
        if (is_ws or request.method not in ("GET", "HEAD", "OPTIONS")) and not origin_ok(request):
            raise web.HTTPForbidden(text="Cross-origin desktop access is not allowed.")
        # Fixed upstream and a leading slash prevent client-controlled proxy destinations.
        path = request.rel_url.raw_path_qs
        target = URL(config.upstream + "/" + path.lstrip("/"), encoded=True)
        if is_ws:
            return await websocket(request, target, expiry)
        headers = filtered_headers(request.headers, request=True)
        headers["Host"] = urlsplit(config.origin).netloc
        headers["X-Forwarded-Proto"] = urlsplit(config.origin).scheme
        try:
            async with client.request(request.method, target, headers=headers,
                                      data=request.content.iter_chunked(65536) if request.can_read_body else None,
                                      allow_redirects=False) as upstream:
                response_headers = filtered_headers(upstream.headers)
                response_headers["Cache-Control"] = "no-store"
                response = web.StreamResponse(status=upstream.status, headers=response_headers)
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (ClientError, asyncio.TimeoutError):
            raise web.HTTPBadGateway(text="The Blender desktop is not ready. Try again shortly.")

    async def health(request):
        return web.json_response({"gateway": "ready"})

    app.router.add_get("/__workstation/health", health)
    app.router.add_get(LOGIN, login)
    app.router.add_post(LOGIN, login)
    app.router.add_route("*", "/{path:.*}", proxy)
    return app


if __name__ == "__main__":
    config = Config(os.environ["PUBLIC_ORIGIN"], os.environ["LOGIN_USER"],
                    os.environ["PASSWORD_HASH"], bytes.fromhex(os.environ["SESSION_KEY"]),
                    os.environ.get("UPSTREAM", "http://blender:3000"))
    web.run_app(create_app(config), host="0.0.0.0", port=8080, access_log=None)
