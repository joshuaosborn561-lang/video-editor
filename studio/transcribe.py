"""Deepgram word timings. Absent key means captions wait."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def transcribe(path: Path) -> dict | None:
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        return None
    url = (
        "https://api.deepgram.com/v1/listen"
        "?model=nova-3&smart_format=true&punctuate=true&utterances=true"
    )
    data = path.read_bytes()
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Token {key}",
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode())
    alternative = payload["results"]["channels"][0]["alternatives"][0]
    words = [
        {"word": word["word"], "start": word["start"], "end": word["end"]}
        for word in alternative.get("words") or []
    ]
    return {"transcript": alternative.get("transcript") or "", "words": words}
