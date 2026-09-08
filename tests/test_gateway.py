import asyncio
import gzip
import importlib.util
from pathlib import Path
import sys
import time
import unittest

from aiohttp import web, WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))
from app import Config, COOKIE, LOGIN, SESSION_SECONDS, create_app, new_session, password_hash

PASSWORD = "test-only-passphrase-not-a-live-secret"


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.archive = gzip.compress(b"Blender asset bytes" * 1000)
        upstream = web.Application()

        async def handler(request):
            self.calls.append(request.path)
            if request.path == "/socket":
                socket = web.WebSocketResponse(protocols=["binary"])
                await socket.prepare(request)
                async for message in socket:
                    if message.type == WSMsgType.BINARY:
                        await socket.send_bytes(message.data)
                    elif message.type == WSMsgType.TEXT:
                        await socket.send_str(message.data)
                return socket
            if request.path == "/asset":
                return web.Response(body=self.archive, headers={"Content-Encoding": "gzip"})
            if request.path == "/upload":
                return web.Response(body=await request.read())
            if request.path == "/headers":
                return web.json_response(dict(request.headers))
            return web.Response(text="real-upstream-placeholder-for-proxy-test-only")

        upstream.router.add_route("*", "/{path:.*}", handler)
        self.upstream = TestServer(upstream)
        await self.upstream.start_server()
        self.config = Config("https://studio.example.org", "studio", password_hash(PASSWORD),
                             b"test-signing-key-32-bytes-long!!!!", str(self.upstream.make_url("")))
        self.client = TestClient(TestServer(create_app(self.config)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.upstream.close()

    def signed(self, token=None):
        return {"Cookie": f"{COOKIE}={token or new_session(self.config)}", "Origin": self.config.origin}

    async def test_unauthenticated_http_and_websocket_never_reach_desktop(self):
        response = await self.client.get("/", allow_redirects=False)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], LOGIN)
        with self.assertRaises(WSServerHandshakeError) as error:
            await self.client.ws_connect("/socket", headers={"Origin": self.config.origin})
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(self.calls, [])

    async def test_login_cookie_and_cross_origin_protection(self):
        body = {"username": "studio", "password": PASSWORD}
        response = await self.client.post(LOGIN, data=body, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status, 403)
        response = await self.client.post(LOGIN, data=body, headers={"Origin": self.config.origin}, allow_redirects=False)
        self.assertEqual(response.status, 303)
        cookie = response.cookies[COOKIE]
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        response = await self.client.get("/", headers=self.signed(cookie.value))
        self.assertEqual(response.status, 200)
        headers = self.signed(cookie.value)
        headers["Origin"] = "https://evil.example"
        self.assertEqual((await self.client.post("/upload", data=b"bad", headers=headers)).status, 403)
        with self.assertRaises(WSServerHandshakeError) as error:
            await self.client.ws_connect("/socket", headers=headers)
        self.assertEqual(error.exception.status, 403)

    async def test_expired_or_forged_cookie_is_rejected(self):
        for token in ["garbage", new_session(self.config)[:-1] + "x",
                      new_session(self.config, time.time() - SESSION_SECONDS - 5)]:
            response = await self.client.get("/asset", headers=self.signed(token))
            self.assertEqual(response.status, 401)
        self.assertEqual(self.calls, [])

    async def test_keyboard_text_and_binary_frames_round_trip(self):
        async with self.client.ws_connect("/socket", headers=self.signed(), protocols=["binary"]) as socket:
            self.assertEqual(socket.protocol, "binary")
            await socket.send_str('keydown,65,1')
            self.assertEqual((await socket.receive(timeout=2)).data, 'keydown,65,1')
            payload = bytes(range(256)) * 2048
            await socket.send_bytes(payload)
            self.assertEqual((await socket.receive(timeout=2)).data, payload)

    async def test_active_socket_closes_at_session_expiry(self):
        token = new_session(self.config, time.time() - SESSION_SECONDS + 2)
        async with self.client.ws_connect("/socket", headers=self.signed(token)) as socket:
            response = await socket.receive(timeout=3)
            self.assertIn(response.type, [WSMsgType.CLOSE, WSMsgType.CLOSED])

    async def test_upload_and_compressed_asset_bytes_are_preserved(self):
        payload = bytes(range(256)) * 4096
        response = await self.client.post("/upload", data=payload, headers=self.signed())
        self.assertEqual(await response.read(), payload)
        response = await self.client.get("/asset", headers=self.signed(), auto_decompress=False)
        self.assertEqual(await response.read(), self.archive)

    async def test_session_and_untrusted_forwarded_headers_do_not_leak_upstream(self):
        headers = self.signed()
        headers.update({"X-Forwarded-For": "spoofed", "Authorization": "Bearer must-not-leak"})
        response = await self.client.get("/headers", headers=headers)
        observed = {k.lower(): v for k, v in (await response.json()).items()}
        for key in ["cookie", "authorization", "x-forwarded-for"]:
            self.assertNotIn(key, observed)

    async def test_repeated_bad_logins_are_limited(self):
        for _ in range(12):
            response = await self.client.post(LOGIN, data={"username": "studio", "password": "bad"},
                                              headers={"Origin": self.config.origin})
            self.assertEqual(response.status, 401)
        response = await self.client.post(LOGIN, data={"username": "studio", "password": "bad"},
                                          headers={"Origin": self.config.origin})
        self.assertEqual(response.status, 429)


if __name__ == "__main__":
    unittest.main()
