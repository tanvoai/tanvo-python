"""Offline tests against a fake API server (standard library only): python -m unittest discover -s tests"""

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import tanvo  # noqa: E402
from tanvo import AuthError, Client, InvalidRequest, QuotaError, ServiceError, WebhookVerificationError, verify_webhook  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
MODELS = [
    {"id": "studio-image-v1", "kind": "image", "name": "Tanvo Image v1", "free_tier": True, "inputs": ["text", "image"], "options": {}, "defaults": {"aspect": "1:1", "resolution": "1K", "format": "PNG"}, "credits_at_defaults": 5, "prompt_max_chars": 2000},
    {"id": "seedream-4", "kind": "image", "name": "Seedream 4.0", "free_tier": False, "inputs": ["text", "image"], "options": {}, "defaults": {"aspect": "1:1", "resolution": "2K"}, "credits_at_defaults": 10, "prompt_max_chars": 5000},
    {"id": "studio-video-v1", "kind": "video", "name": "Tanvo Video v1", "free_tier": True, "inputs": ["text", "image"], "options": {}, "defaults": {"aspect": "16:9", "resolution": "480p", "duration": 5}, "credits_at_defaults": 20, "prompt_max_chars": 2000},
]
APPS = [{
    "slug": "ai-pet-portrait-generator", "kind": "image", "name": "Pet Portrait Generator", "description": "A painted portrait.", "category": "Pets", "model": "seedream-4",
    "url": "https://tanvo.ai/image/ai-pet-portrait-generator", "inputs": [{"id": "in1", "label": "Pet photo", "hint": "", "accept": "image/*", "min": 1, "max": 1}],
    "looks": [{"id": "renaissance", "name": "Renaissance oil", "prompt": "Paint the pet as a Renaissance oil portrait.", "model": "seedream-4", "options": {}, "example": {"url": "https://x/e.webp", "kind": "image"}, "url": "https://tanvo.ai/image/ai-pet-portrait-generator?look=renaissance"},
              {"id": "royal", "name": "Royal monarch", "prompt": "Paint the pet as a monarch.", "model": "seedream-4", "options": {"aspect": "3:4"}, "example": None, "url": "https://tanvo.ai/image/ai-pet-portrait-generator?look=royal"}],
}]


class Fake:
    """State shared with the handler: what was asked, and which failures to inject next."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.requests = []
        self.runs = {}
        self.by_key = {}
        self.fail_next = []  # (status, error, extra_headers)
        self.polls_until_done = 1
        self.uploaded = {}


FAKE = Fake()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, payload, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _handle(self, method):
        raw = self._body()
        FAKE.requests.append((method, self.path, dict(self.headers), raw))
        if self.path.startswith("/put/"):
            FAKE.uploaded[self.path] = (self.headers.get("Content-Type"), raw)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/files/"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
            return
        if FAKE.fail_next:
            status, error, headers = FAKE.fail_next.pop(0)
            return self._send(status, {"error": error, "message": f"injected {error}"}, headers)
        auth = self.headers.get("Authorization")
        if auth and auth != "Bearer sk_good":
            return self._send(401, {"error": "invalid_api_key", "message": "That API key is not valid."})
        path = self.path.split("?")[0]
        if method == "GET" and path == "/api/v1/models":
            return self._send(200, {"models": MODELS})
        if method == "GET" and path == "/api/v1/apps":
            return self._send(200, {"apps": APPS})
        if method == "GET" and path == "/api/v1/me":
            return self._send(200, {"tier": "paid" if auth else "anonymous", "credits": 900 if auth else 5, "watermarked": not auth, "concurrency": 3 if auth else 1})
        if method == "POST" and path == "/api/v1/generations":
            b = json.loads(raw)
            if not auth and b["model"] != "studio-image-v1":
                return self._send(403, {"error": "needs_api_key", "message": "The free tier covers the house image engine only."})
            if b["options"].get("resolution") == "8K":
                return self._send(400, {"error": "invalid", "message": "That model or setting is not available.", "issues": ["options.resolution: 8K"]})
            key = b.get("requestKey")
            if key in FAKE.by_key:
                return self._send(202, {"generation": FAKE.runs[FAKE.by_key[key]]["g"]})
            gid = f"gen{len(FAKE.runs) + 1}"
            g = {"id": gid, "kind": b["kind"], "model": b["model"], "prompt": b["prompt"], "options": b["options"], "cost": 5, "status": "PROCESSING", "error": None, "createdAt": 1790000000000 + len(FAKE.runs), "outputs": []}
            FAKE.runs[gid] = {"g": g, "polls": 0, "body": b}
            FAKE.by_key[key] = gid
            return self._send(202, {"generation": g})
        if method == "GET" and path.startswith("/api/v1/generations/"):
            r = FAKE.runs.get(path.rsplit("/", 1)[1])
            if not r:
                return self._send(404, {"error": "not_found", "message": "No such generation."})
            r["polls"] += 1
            if r["polls"] >= FAKE.polls_until_done:
                host = f"http://127.0.0.1:{self.server.server_address[1]}"
                r["g"] = {**r["g"], "status": "SUCCEEDED", "outputs": [{"url": f"{host}/files/{r['g']['id']}/0.png", "mime": "image/png"}]}
            return self._send(200, {"generation": r["g"]})
        if method == "GET" and path == "/api/v1/generations":
            gs = sorted((r["g"] for r in FAKE.runs.values()), key=lambda g: -g["createdAt"])[:2]
            return self._send(200, {"generations": gs, "next_before": gs[-1]["createdAt"] if gs else None})
        if method == "POST" and path == "/api/v1/uploads":
            b = json.loads(raw)
            host = f"http://127.0.0.1:{self.server.server_address[1]}"
            return self._send(200, {"upload_url": f"{host}/put/{len(FAKE.uploaded)}", "url": f"https://media.tanvo.ai/uploads/u/{len(FAKE.uploaded)}.png", "expires_in": 300, "_mime": b["mime"]})
        self._send(404, {"error": "not_found", "message": self.path})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FAKE.reset()
        # Polling and retry delays are real sleeps; make them instant.
        self.sleep = mock.patch("tanvo._client.time.sleep", lambda s: None)
        self.sleep.start()
        os.environ.pop("TANVO_API_KEY", None)

    def tearDown(self):
        self.sleep.stop()

    def client(self, key=None):
        return Client(key, base_url=self.base)

    def test_anonymous_free_image(self):
        c = self.client()
        self.assertEqual(c.me().tier, "anonymous")
        g = c.generate_image("a cat")
        self.assertTrue(g.succeeded)
        self.assertEqual(g.cost, 5)
        post = next(r for r in FAKE.requests if r[0] == "POST" and r[1] == "/api/v1/generations")
        sent = json.loads(post[3])
        self.assertEqual(sent["options"], {"aspect": "1:1", "resolution": "1K", "format": "PNG"})  # model defaults filled in
        self.assertTrue(sent["requestKey"])
        headers = post[2]
        self.assertTrue(headers["X-Anon-Id"].startswith("py-"))
        self.assertTrue(headers["User-Agent"].startswith("tanvo-python/"))

    def test_free_tier_refuses_other_models_with_a_hint(self):
        with self.assertRaises(QuotaError) as e:
            self.client().generate_video("a fox")
        self.assertEqual(e.exception.code, "needs_api_key")
        self.assertIn("settings/apikeys", str(e.exception))

    def test_key_options_override_defaults(self):
        c = self.client("sk_good")
        g = c.generate_image("a cat", model="seedream-4", aspect="16:9")
        self.assertTrue(g.succeeded)
        self.assertEqual(json.loads(FAKE.requests[-2][3])["options"], {"aspect": "16:9", "resolution": "2K"})
        self.assertEqual(FAKE.requests[-1][2]["Authorization"], "Bearer sk_good")

    def test_request_key_is_idempotent(self):
        c = self.client("sk_good")
        a = c.generate_image("a cat", request_key="job-1", wait=False)
        b = c.generate_image("a cat", request_key="job-1", wait=False)
        self.assertEqual(a.id, b.id)
        self.assertEqual(len(FAKE.runs), 1)

    def test_video_returns_at_once_then_wait(self):
        c = self.client("sk_good")
        FAKE.polls_until_done = 3
        g = c.generate_video("a fox in snow")
        self.assertEqual(g.status, "PROCESSING")
        self.assertTrue(c.wait(g.id, timeout=60).succeeded)

    def test_wait_timeout_returns_last_state(self):
        c = self.client("sk_good")
        FAKE.polls_until_done = 10_000
        g = c.generate_video("a fox")
        clock = iter(range(0, 10_000, 30))
        with mock.patch("tanvo._client.time.monotonic", lambda: next(clock)):
            last = c.wait(g.id, timeout=60)
        self.assertEqual(last.status, "PROCESSING")

    def test_errors_map_to_classes(self):
        with self.assertRaises(AuthError) as e:
            self.client("sk_bad").me()
        self.assertEqual(e.exception.code, "invalid_api_key")
        with self.assertRaises(InvalidRequest) as e:
            self.client("sk_good").generate_image("x", resolution="8K")
        self.assertEqual(e.exception.issues, ["options.resolution: 8K"])

    def test_service_errors_retry_with_the_same_request_key(self):
        c = self.client("sk_good")
        c.models()
        FAKE.fail_next = [(502, "provider", None), (500, "server_error", None)]
        g = c.generate_image("a cat", wait=False)
        posts = [json.loads(r[3])["requestKey"] for r in FAKE.requests if r[0] == "POST" and r[1] == "/api/v1/generations"]
        self.assertEqual(len(posts), 3)
        self.assertEqual(len(set(posts)), 1)
        self.assertEqual(g.status, "PROCESSING")

    def test_service_error_gives_up_after_two_retries(self):
        c = self.client("sk_good")
        c.models()
        FAKE.fail_next = [(502, "provider", None)] * 3
        with self.assertRaises(ServiceError):
            c.generate_image("a cat", wait=False)

    def test_rate_limit_waits_once(self):
        c = self.client("sk_good")
        c.models()
        FAKE.fail_next = [(429, "rate_limited", {"Retry-After": "2"})]
        self.assertEqual(c.me().tier, "paid")

    def test_busy_waits_for_a_slot_when_asked(self):
        c = self.client("sk_good")
        c.models()
        FAKE.fail_next = [(409, "busy", None)]
        with self.assertRaises(QuotaError):
            c.generate_image("a cat", wait=False)
        FAKE.fail_next = [(409, "busy", None)]
        self.assertEqual(c.generate_image("a cat", wait=False, wait_for_slot=True).status, "PROCESSING")

    def test_local_images_are_uploaded(self):
        c = self.client("sk_good")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(PNG)
        try:
            g = c.generate_image("make it winter", model="seedream-4", images=[f.name, "https://example.com/a.jpg", PNG], wait=False)
        finally:
            os.unlink(f.name)
        sent = json.loads(next(r[3] for r in FAKE.requests if r[1] == "/api/v1/generations"))
        self.assertEqual(sent["imageUrls"][1], "https://example.com/a.jpg")
        self.assertTrue(all(u.startswith("https://media.tanvo.ai/uploads/") for u in (sent["imageUrls"][0], sent["imageUrls"][2])))
        self.assertEqual([m for m, _ in FAKE.uploaded.values()], ["image/png", "image/png"])
        self.assertEqual(g.status, "PROCESSING")

    def test_generate_from_app_uses_the_look(self):
        c = self.client("sk_good")
        with self.assertRaises(ValueError):
            c.generate_from_app("ai-pet-portrait-generator", look="royal")
        g = c.generate_from_app("ai-pet-portrait-generator", look="royal", photos=["https://example.com/rex.jpg"], details="Rex, a very good boy")
        self.assertTrue(g.succeeded)
        sent = json.loads(next(r[3] for r in FAKE.requests if r[1] == "/api/v1/generations"))
        self.assertEqual(sent["model"], "seedream-4")
        self.assertEqual(sent["options"], {"aspect": "3:4", "resolution": "2K"})
        self.assertTrue(sent["prompt"].startswith("Paint the pet as a monarch.") and sent["prompt"].endswith("This one is for: Rex, a very good boy."))
        with self.assertRaises(KeyError):
            c.app("ai-pet-portrait-generator").look("nope")

    def test_list_and_download(self):
        c = self.client("sk_good")
        for i in range(3):
            c.generate_image(f"cat {i}", wait=False)
        items, nxt = c.list(limit=2)
        self.assertEqual(len(items), 2)
        self.assertEqual(nxt, items[-1].created_at)
        g = c.wait(items[0].id)
        with tempfile.TemporaryDirectory() as d:
            path = c.download(g, d)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), PNG)

    def test_network_error_is_a_service_error(self):
        with self.assertRaises(ServiceError) as e:
            Client("sk_good", base_url="http://127.0.0.1:9").me()
        self.assertEqual(e.exception.code, "network")


class WebhookTest(unittest.TestCase):
    secret = "whsec_" + base64.b64encode(b"k" * 24).decode()
    body = json.dumps({"id": "gen1", "kind": "image", "model": "studio-image-v1", "prompt": "p", "options": {}, "cost": 5, "status": "SUCCEEDED", "error": None, "createdAt": 1, "outputs": [{"url": "https://x/0.png", "mime": "image/png"}]})

    def sign(self, ts, body=None):
        mac = hmac.new(b"k" * 24, f"msg_1.{ts}.{body or self.body}".encode(), hashlib.sha256).digest()
        return {"webhook-id": "msg_1", "webhook-timestamp": str(ts), "webhook-signature": f"v1,{base64.b64encode(mac).decode()}"}

    def test_valid(self):
        g = verify_webhook(self.secret, self.sign(int(time.time())), self.body.encode())
        self.assertEqual((g.id, g.url), ("gen1", "https://x/0.png"))
        self.assertEqual(Client.verify_webhook(self.secret, {k.title(): v for k, v in self.sign(int(time.time())).items()}, self.body).id, "gen1")

    def test_tampered_or_stale(self):
        h = self.sign(int(time.time()))
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(self.secret, h, self.body.replace("gen1", "gen2"))
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(self.secret, self.sign(int(time.time()) - 600), self.body)
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(self.secret, {}, self.body)

    def test_version(self):
        self.assertRegex(tanvo.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
