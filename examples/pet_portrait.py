"""python examples/pet_portrait.py rex.jpg: a royal portrait of your pet (needs TANVO_API_KEY)."""
import sys

import tanvo

client = tanvo.Client()
app = client.app("ai-pet-portrait-generator")
print("Looks:", ", ".join(f"{l.id} ({l.name})" for l in app.looks))
result = client.generate_from_app(app.slug, look="royal", photos=[sys.argv[1]])
print(result.status, result.url)
print("saved to", client.download(result, "out/"))
