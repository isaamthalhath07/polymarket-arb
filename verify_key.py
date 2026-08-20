"""Verify the local LLM server is reachable and the configured model is loaded."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI

host = os.environ.get("OLLAMA_HOST", "http://localhost:11434/v1")
model = os.environ.get("CLASSIFIER_MODEL", "qwen2.5:3b-instruct")
# First call cold-loads the model into RAM; on CPU that can take minutes.
client = OpenAI(base_url=host, api_key="ollama", timeout=600.0)

print(f"Calling {model} @ {host} (first call cold-loads the model; may take a few min)...")
try:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with one word: ok"}],
        temperature=0.0,
        max_tokens=10,
        extra_body={"keep_alive": "30m"},
    )
    print(f"OK ({model} @ {host}):", (resp.choices[0].message.content or "").strip())
except Exception as exc:
    msg = str(exc)
    print(f"FAIL: {type(exc).__name__}: {msg}", file=sys.stderr)
    if "Connection" in msg or "refused" in msg.lower():
        print("\nIs Ollama running? Try: ollama list", file=sys.stderr)
    if "model" in msg.lower() and ("not found" in msg.lower() or "404" in msg):
        print(f"\nPull the model: ollama pull {model}", file=sys.stderr)
    sys.exit(2)
