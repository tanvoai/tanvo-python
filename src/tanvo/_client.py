from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from ._errors import ServiceError, TanvoError, error_for
from ._types import Account, App, Generation, Model
from ._webhook import verify_webhook

__version__ = "0.1.1"

DEFAULT_BASE_URL = "https://tanvo.ai"
ImageInput = Union[str, os.PathLike, bytes]
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".mp4": "video/mp4", ".mov": "video/quicktime", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


class Client:
    """Tanvo API client.

    With no key (argument or TANVO_API_KEY) it runs on the anonymous free tier: the house image engine only,
    watermarked, about one image per client. Create a key at https://tanvo.ai/settings/apikeys for everything else.
    """

    def __init__(self, api_key: Optional[str] = None, *, anon_id: Optional[str] = None, base_url: Optional[str] = None, timeout: float = 60.0):
        self.api_key = api_key or os.environ.get("TANVO_API_KEY") or None
        self.anon_id = None if self.api_key else (anon_id or f"py-{uuid.uuid4()}")
        self.base_url = (base_url or os.environ.get("TANVO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._models: Optional[List[Model]] = None
        self._apps: Optional[List[App]] = None

    # ─── HTTP ──────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, *, retries: int = 0) -> Dict[str, Any]:
        url = f"{self.base_url}/api/v1{path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": f"tanvo-python/{__version__}"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            headers["x-anon-id"] = self.anon_id or ""
        data = json.dumps(body).encode() if body is not None else None
        delays = [1, 3]
        rate_limited_once = False
        attempt = 0
        while True:
            try:
                req = urllib.request.Request(url, data=data, method=method, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    raw = res.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                err = self._error(e)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                err = ServiceError("network", f"Could not reach {self.base_url}: {getattr(e, 'reason', e)}", 0)
            if err.code == "rate_limited" and not rate_limited_once:
                rate_limited_once = True
                time.sleep(min(err.retry_after or 5, 60))
                continue
            if isinstance(err, ServiceError) and attempt < retries:
                time.sleep(delays[min(attempt, len(delays) - 1)])
                attempt += 1
                continue
            raise err

    def _error(self, e: urllib.error.HTTPError) -> TanvoError:
        try:
            payload = json.loads(e.read() or b"{}")
        except ValueError:
            payload = {}
        retry = e.headers.get("Retry-After") if e.headers else None
        return error_for(payload.get("error") or f"http_{e.code}", payload.get("message") or f"HTTP {e.code}", e.code, payload.get("issues"), float(retry) if retry and retry.isdigit() else None, has_key=bool(self.api_key))

    # ─── Catalogue and account ─────────────────────────────────────────────

    def models(self, kind: Optional[str] = None) -> List[Model]:
        """Every model with its options, defaults and credit cost at defaults. Cached on the client."""
        if self._models is None:
            self._models = [Model.from_dict(m) for m in self._request("GET", "/models", retries=2)["models"]]
        return [m for m in self._models if kind is None or m.kind == kind]

    def model(self, model_id: str) -> Model:
        for m in self.models():
            if m.id == model_id:
                return m
        raise KeyError(f"Unknown model {model_id!r}. See client.models().")

    def apps(self, kind: Optional[str] = None) -> List[App]:
        """Ready-made apps (pet portraits, figurines, trend videos, songs…) with their preset looks. Cached on the client."""
        if self._apps is None:
            self._apps = [App.from_dict(a) for a in self._request("GET", "/apps", retries=2)["apps"]]
        return [a for a in self._apps if kind is None or a.kind == kind]

    def app(self, slug: str) -> App:
        for a in self.apps():
            if a.slug == slug:
                return a
        raise KeyError(f"Unknown app {slug!r}. See client.apps().")

    def me(self) -> Account:
        d = self._request("GET", "/me", retries=2)
        return Account(tier=d.get("tier", ""), credits=d.get("credits", 0), watermarked=bool(d.get("watermarked")), concurrency=d.get("concurrency", 1), email=d.get("email"))

    # ─── Generation ────────────────────────────────────────────────────────

    def _options(self, model_id: str, given: Dict[str, Any]) -> Dict[str, Any]:
        """The model's defaults, overridden by whatever the caller set."""
        try:
            base = dict(self.model(model_id).defaults)
        except KeyError:
            base = {}
        base.update({k: v for k, v in given.items() if v is not None})
        return base

    def _submit(self, body: Dict[str, Any], wait: bool, timeout: float, wait_for_slot: bool) -> Generation:
        body = {k: v for k, v in body.items() if v not in (None, [], "")}
        body.setdefault("requestKey", str(uuid.uuid4()))  # makes the retries below safe: a repeat returns the first run
        deadline = time.monotonic() + timeout
        while True:
            try:
                g = Generation.from_dict(self._request("POST", "/generations", body, retries=2)["generation"])
                break
            except TanvoError as e:
                if e.code == "busy" and wait_for_slot and time.monotonic() + 5 < deadline:
                    time.sleep(5)
                    continue
                raise
        if wait:
            return self.wait(g.id, timeout=max(1.0, deadline - time.monotonic()), every=6 if g.kind == "video" else 4)
        return g

    def generate_image(
        self, prompt: str, *, model: str = "studio-image-v1", aspect: Optional[str] = None, resolution: Optional[str] = None, format: Optional[str] = None,
        images: Sequence[ImageInput] = (), negative_prompt: Optional[str] = None, wait: bool = True, timeout: float = 180, request_key: Optional[str] = None,
        webhook_url: Optional[str] = None, wait_for_slot: bool = False,
    ) -> Generation:
        """Text to image, or an edit / combination when `images` are given (https URLs, local paths or bytes)."""
        return self._submit({
            "kind": "image", "model": model, "prompt": prompt, "negativePrompt": negative_prompt,
            "options": self._options(model, {"aspect": aspect, "resolution": resolution, "format": format}),
            "imageUrls": [self._ref(i) for i in images], "requestKey": request_key, "webhookUrl": webhook_url,
        }, wait, timeout, wait_for_slot)

    def generate_video(
        self, prompt: str, *, model: str = "studio-video-v1", aspect: Optional[str] = None, resolution: Optional[str] = None, duration: Optional[int] = None,
        audio: Optional[bool] = None, tier: Optional[str] = None, image: Optional[ImageInput] = None, end_image: Optional[ImageInput] = None,
        negative_prompt: Optional[str] = None, wait: bool = False, timeout: float = 600, request_key: Optional[str] = None, webhook_url: Optional[str] = None, wait_for_slot: bool = False,
    ) -> Generation:
        """Text to video, or image to video from `image` (optionally ending on `end_image`). Returns at once unless wait=True.
        `tier` picks a model's quality tier where it has one (Veo 3.1: "Lite", "Fast" or "Quality")."""
        return self._submit({
            "kind": "video", "model": model, "prompt": prompt, "negativePrompt": negative_prompt,
            "options": self._options(model, {"aspect": aspect, "resolution": resolution, "duration": duration, "audio": audio, "tier": tier}),
            "imageUrls": [self._ref(image)] if image is not None else [], "endImageUrl": self._ref(end_image) if end_image is not None else None,
            "requestKey": request_key, "webhookUrl": webhook_url,
        }, wait, timeout, wait_for_slot)

    def generate_music(
        self, prompt: str, *, mode: str = "Describe", style: Optional[str] = None, title: Optional[str] = None, vocal: Optional[str] = None,
        model: str = "suno-v6", wait: bool = True, timeout: float = 300, request_key: Optional[str] = None, webhook_url: Optional[str] = None,
        wait_for_slot: bool = False,
    ) -> Generation:
        """Two takes of a song. mode: "Describe" (prompt describes the song), "Lyrics" (prompt is your lyrics) or "Instrumental"."""
        return self._submit({
            "kind": "music", "model": model, "prompt": prompt,
            "options": {k: v for k, v in {"tier": mode, "resolution": "song", "style": style, "title": title, "vocal": vocal}.items() if v is not None},
            "requestKey": request_key, "webhookUrl": webhook_url,
        }, wait, timeout, wait_for_slot)

    def generate_from_app(
        self, app: str, *, look: Optional[str] = None, photos: Sequence[ImageInput] = (), details: Optional[str] = None, wait: Optional[bool] = None,
        timeout: Optional[float] = None, request_key: Optional[str] = None, webhook_url: Optional[str] = None, wait_for_slot: bool = False,
    ) -> Generation:
        """Runs one of an app's preset looks on your photos (in the order of app.inputs), e.g.
        client.generate_from_app("ai-pet-portrait-generator", look="royal", photos=["rex.jpg"])."""
        a = self.app(app)
        l = a.look(look)
        needed = sum(1 for i in a.inputs if i.min > 0)
        if len(photos) < needed:
            raise ValueError(f"{a.name} needs {needed} photo(s): {', '.join(i.label for i in a.inputs if i.min > 0)}.")
        prompt = f"{l.prompt}\n\nThis one is for: {details.strip()}." if details else l.prompt
        is_video = a.kind == "video"
        return self._submit({
            "kind": a.kind, "model": l.model, "prompt": prompt, "options": self._options(l.model, dict(l.options)),
            "imageUrls": [self._ref(p) for p in photos] if a.kind != "music" else [], "requestKey": request_key, "webhookUrl": webhook_url,
        }, (not is_video) if wait is None else wait, timeout or (600 if is_video else 300 if a.kind == "music" else 180), wait_for_slot)

    # ─── Runs ──────────────────────────────────────────────────────────────

    def get(self, generation_id: str) -> Generation:
        return Generation.from_dict(self._request("GET", f"/generations/{urllib.parse.quote(generation_id)}", retries=2)["generation"])

    def wait(self, generation_id: str, timeout: float = 600, every: float = 4) -> Generation:
        """Polls until the run finishes. On timeout returns the last state seen (still running) instead of raising."""
        deadline = time.monotonic() + timeout
        time.sleep(min(2, timeout))
        last: Optional[Generation] = None
        while True:
            try:
                last = self.get(generation_id)
                if last.done:
                    return last
            except ServiceError:
                pass  # one failed read is not a failed run
            if time.monotonic() + every > deadline:
                return last if last is not None else self.get(generation_id)
            time.sleep(every)

    def list(self, limit: int = 20, before: Optional[int] = None) -> Tuple[List[Generation], Optional[int]]:
        q = urllib.parse.urlencode({k: v for k, v in {"limit": limit, "before": before}.items() if v is not None})
        d = self._request("GET", f"/generations?{q}", retries=2)
        return [Generation.from_dict(g) for g in d.get("generations", [])], d.get("next_before")

    # ─── Files ─────────────────────────────────────────────────────────────

    def upload(self, file: Union[str, os.PathLike, bytes], mime: Optional[str] = None) -> str:
        """Uploads a local file (path or bytes) and returns a URL usable in images= / image=."""
        if isinstance(file, bytes):
            data = file
            if not mime:
                raise ValueError("Pass mime= when uploading bytes, e.g. mime='image/png'.")
        else:
            p = Path(file).expanduser()
            data = p.read_bytes()
            mime = mime or _MIME.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0]
            if not mime:
                raise ValueError(f"Cannot tell the file type of {p.name}; pass mime=.")
        slot = self._request("POST", "/uploads", {"mime": mime, "bytes": len(data)})
        req = urllib.request.Request(slot["upload_url"], data=data, method="PUT", headers={"Content-Type": mime})
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 120)):
                pass
        except urllib.error.HTTPError as e:
            raise ServiceError("upload_failed", f"Upload failed with HTTP {e.code}.", e.code) from None
        return slot["url"]

    def _ref(self, ref: ImageInput) -> str:
        if isinstance(ref, str) and ref.startswith("https://"):
            return ref
        if isinstance(ref, str) and ref.startswith("http://"):
            if self.base_url.startswith("http://"):
                return ref  # a local deployment can fetch plain http
            raise ValueError(f"Only https URLs can be fetched by the service: {ref}")
        if isinstance(ref, bytes):
            return self.upload(ref, mime="image/png" if ref[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if ref[:3] == b"\xff\xd8\xff" else "image/webp")
        return self.upload(ref)

    def download(self, generation: Union[Generation, str], path: Union[str, os.PathLike] = ".", index: int = 0) -> str:
        """Saves one output to `path` (a folder or a file name) and returns the file path."""
        g = self.get(generation) if isinstance(generation, str) else generation
        if not g.outputs:
            raise ValueError(f"Generation {g.id} has no outputs (status {g.status}).")
        url = g.outputs[index].url
        target = Path(path).expanduser()
        if target.is_dir() or str(path).endswith(("/", os.sep)):
            target.mkdir(parents=True, exist_ok=True)
            target = target / f"tanvo-{g.id}-{index}{Path(urllib.parse.urlparse(url).path).suffix or '.bin'}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"tanvo-python/{__version__}"}), timeout=max(self.timeout, 120)) as res:
            target.write_bytes(res.read())
        return str(target)

    verify_webhook = staticmethod(verify_webhook)
