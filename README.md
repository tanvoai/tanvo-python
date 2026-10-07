# Tanvo for Python

[![PyPI](https://img.shields.io/pypi/v/tanvo)](https://pypi.org/project/tanvo/) [![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

The Python client for [Tanvo](https://tanvo.ai/?utm_source=github&utm_medium=referral), an AI image, video and music studio. Generate images on Nano Banana, Seedream, GPT Image and Qwen, video on Veo, Kling, Seedance and Wan, and songs on Suno, or run one of 80 ready-made photo apps (pet portraits, figurines, old photo restoration, trend videos) on your own photos.

- No dependencies: only the standard library. Python 3.9+.
- **Works without an API key** on the free tier (house image engine, watermarked).
- Local files are uploaded for you; results download with one call.
- Retries that never double-charge (every submit carries an idempotency key).

```bash
pip install tanvo
```

## Quick start

```python
import tanvo

client = tanvo.Client()  # reads TANVO_API_KEY; without one it runs on the free tier
image = client.generate_image("a paper lantern floating over a misty lake at dawn", aspect="16:9")
print(image.url)
client.download(image, "out/")
```

Create a key under [Settings → API keys](https://tanvo.ai/settings/apikeys?utm_source=github&utm_medium=referral) to use every model, get clean output and have runs land in your history.

```python
client = tanvo.Client("sk_...")  # or export TANVO_API_KEY=sk_...
```

## Ready-made apps

Each app has preset looks: a tuned prompt, model and settings with a real example output.

```python
for app in client.apps(kind="image")[:5]:
    print(app.slug, "-", app.name, [look.id for look in app.looks])

portrait = client.generate_from_app(
    "ai-pet-portrait-generator",
    look="royal",
    photos=["rex.jpg"],          # local path, https URL or bytes, in the order of app.inputs
    details="Rex, a very good boy",
)
client.download(portrait, "out/")
```

## Images

```python
# Text to image on any model (see client.models("image") for ids, options and prices)
img = client.generate_image("isometric cutaway of a tiny ramen shop", model="nano-banana-2", resolution="2K")

# Edit or combine photos: describe what to change and what to keep
edit = client.generate_image(
    "change the jacket to dark green; keep the face, pose and background exactly as they are",
    model="nano-banana-2",
    images=["portrait.jpg"],
)
```

## Video

Video takes one to several minutes, so `generate_video` returns at once by default.

```python
job = client.generate_video(
    "slow dolly-in on a lighthouse in a storm, waves crashing, cinematic",
    model="kling-3-0", aspect="16:9", resolution="720p", duration=5,
)
clip = client.wait(job.id)        # or pass wait=True above
client.download(clip, "out/")

# Image to video, with an optional end frame on models that support it
client.generate_video("the cat turns its head and blinks", model="seedance-2-5", image="cat.jpg", wait=True)
```

## Songs

Two takes per run, each an MP3 with cover art, a title and the lyrics as sung.

```python
song = client.generate_music(
    "an upbeat birthday song for my sister Ana: surfing, terrible puns, a big singalong chorus",
    style="acoustic pop, hand claps, warm female vocal",
)
for take in song.outputs:
    print(take.title, take.seconds, take.url, take.cover)

client.generate_music("[Verse]\n...\n[Chorus]\n...", mode="Lyrics", title="Ana Rides Again", vocal="f")
client.generate_music("mellow lo-fi study beat, vinyl crackle, 75 bpm", mode="Instrumental")
```

## Runs, history and webhooks

```python
g = client.get("cm...")                # one run
items, next_before = client.list(limit=20)

# Skip polling: have the result POSTed to you when it finishes
client.generate_video("...", model="veo-3-1", webhook_url="https://example.com/hooks/tanvo")

# In your web handler, verify with the secret from Settings → Webhooks (pass the raw body)
generation = tanvo.verify_webhook(secret, request.headers, request.get_data())
```

Webhooks can arrive more than once; de-duplicate on the `webhook-id` header.

## Errors

| Exception | When | What to do |
|---|---|---|
| `InvalidRequest` | Bad option, refused prompt, file too large | Fix the input |
| `AuthError` | Missing or invalid key | Check the key |
| `QuotaError` | Out of credits, free tier limit, `busy` | Top up, add a key, or wait (`wait_for_slot=True` waits for a free slot) |
| `ServiceError` | Upstream model or network failure | Already retried twice with the same request key; try again later |

All carry `.code` (stable string), `.message` and `.status`. Failed runs are refunded automatically.

## Settings

| | |
|---|---|
| `TANVO_API_KEY` | API key (or `Client(api_key=...)`) |
| `TANVO_BASE_URL` | Another deployment (default `https://tanvo.ai`) |

## Documentation

Full API reference (every endpoint, field, error and limit): [tanvo.ai/developers/api](https://tanvo.ai/developers/api?utm_source=github&utm_medium=referral). Machine-readable spec: [tanvo.ai/openapi.yaml](https://tanvo.ai/openapi.yaml).

## Also from Tanvo

- [tanvo-mcp](https://github.com/tanvoai/tanvo-mcp): the same apps and models inside Claude, Cursor and other MCP clients.
- [tanvo-js](https://github.com/tanvoai/tanvo-js): the TypeScript / JavaScript client.

## License

MIT
