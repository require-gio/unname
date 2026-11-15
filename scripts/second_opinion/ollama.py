"""Small client for Ollama on 127.0.0.1 – only local models."""

from __future__ import annotations

import json
import urllib.request

OLLAMA = "http://127.0.0.1:11434/api/chat"


def check_local(model: str) -> None:
    """Models with ``:cloud`` forward requests to the internet – never for this app."""

    if "cloud" in model.lower():
        raise SystemExit(f"{model} ist ein Cloud-Modell; es werden nur lokale Modelle benutzt.")


def chat(
    model: str,
    system: str,
    user: str,
    *,
    temperature: float = 0.7,
    seed: int | None = None,
    json_format: dict | None = None,
    timeout: float = 900,
) -> str:
    check_local(model)
    options: dict = {"temperature": temperature, "num_ctx": 8192}
    if seed is not None:
        options["seed"] = seed
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "think": False,
        "options": options,
    }
    if json_format is not None:
        body["format"] = json_format
    http_req = urllib.request.Request(
        OLLAMA,
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(http_req, timeout=timeout) as response:
        return json.load(response)["message"]["content"]
