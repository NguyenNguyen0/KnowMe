"""Local LLM (Qwen via llama-server, OpenAI-compatible API)."""

import json
import urllib.request

BASE_URL = "http://localhost:8080/v1"


def generate(
    prompt: str, base_url: str = BASE_URL, temperature: float = 0.2, max_tokens: int = 1024
) -> str:
    # max_tokens: a small model can loop; without a cap one request can hold a slot for minutes.
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["choices"][0]["message"]["content"]
