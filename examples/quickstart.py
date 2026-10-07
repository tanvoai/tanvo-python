"""python examples/quickstart.py: one free image, no API key needed."""
import tanvo

client = tanvo.Client()
image = client.generate_image("a paper lantern floating over a misty lake at dawn", aspect="16:9")
print(image.status, image.url)
print("saved to", client.download(image, "out/"))
