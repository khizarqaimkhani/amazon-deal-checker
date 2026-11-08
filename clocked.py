"""Small Clocked API client used by Deal Checker.

Uses plain HTTPS calls (documented at https://clocked.rezolve.ai/docs) so the
request/response shapes stay visible for anyone reading the code.
"""

import base64
import io
import threading
import time
import uuid

import requests
from PIL import Image

BASE_URL = "https://clocked.rezolve.ai/clocked"
MAX_IMAGE_BYTES = 2_200_000  # API limit is 2.4 MB decoded; stay under it


class ClockedError(Exception):
    def __init__(self, status, body):
        super().__init__(f"Clocked API error {status}: {body}")
        self.status = status
        self.body = body


class Clocked:
    def __init__(self, api_key, timeout=120):
        self.api_key = api_key
        self.timeout = timeout
        self.credits_used = 0.0
        self.calls = []  # (endpoint, credits, model_ms) for the "under the hood" panel
        self._lock = threading.Lock()

    def post(self, endpoint, payload, retries=2):
        idem = str(uuid.uuid4())
        for attempt in range(retries + 1):
            try:
                r = requests.post(
                    f"{BASE_URL}/{endpoint}",
                    json=payload,
                    timeout=self.timeout,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Idempotency-Key": idem,
                        "Content-Type": "application/json",
                    },
                )
            except requests.RequestException as e:
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ClockedError(0, str(e))

            if (r.status_code == 429 or r.status_code >= 500) and attempt < retries:
                try:
                    if r.json().get("retryAsNew"):
                        idem = str(uuid.uuid4())
                except ValueError:
                    pass
                time.sleep(1.5 * (attempt + 1))
                continue

            if not r.ok:
                try:
                    body = r.json()
                except ValueError:
                    body = r.text[:500]
                raise ClockedError(r.status_code, body)

            credits = r.headers.get("X-Clocked-Credits")
            with self._lock:
                try:
                    self.credits_used += float(credits) if credits else 0.0
                except ValueError:
                    pass
                self.calls.append(
                    {
                        "endpoint": endpoint,
                        "credits": credits,
                        "model_ms": r.headers.get("X-Clocked-Model-Ms"),
                    }
                )
            return r.json()


def image_to_data_url(raw_bytes):
    """Shrink/convert an uploaded image so it fits the API image limits."""
    img = Image.open(io.BytesIO(raw_bytes))
    img = img.convert("RGB")
    img.thumbnail((2400, 2400))
    quality = 88
    while True:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()
        if len(data) <= MAX_IMAGE_BYTES or quality <= 50:
            break
        quality -= 8
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def pdf_to_data_url(raw_bytes):
    return "data:application/pdf;base64," + base64.b64encode(raw_bytes).decode()
