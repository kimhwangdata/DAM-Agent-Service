"""Upload queue + uploader thread — presigned two-step flow (design §2, §5).

No AWS credentials and no boto3 on the device (ADR-0003): each frame is
uploaded by asking the upload-signer for a presigned PUT URL (authenticated
with the device token), then PUTting the JPEG over plain HTTPS with stdlib
urllib. The head item is retried in place with exponential backoff and a
fresh presign per attempt (URLs expire) — equivalent to re-queue-at-front
with a single uploader thread.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC

from agent.capture import CaptureItem, format_hhmmssfff
from agent.config import Settings

log = logging.getLogger(__name__)

BACKOFF_INITIAL_S = 1.0
BACKOFF_CAP_S = 60.0
CONTENT_TYPE = "image/jpeg"
SIGN_TIMEOUT_S = 30
PUT_TIMEOUT_S = 60


class Uploader:
    def __init__(
        self,
        settings: Settings,
        *,
        urlopen: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._urlopen = urlopen
        self._sleep = sleep
        self._queue: queue.Queue[CaptureItem] = queue.Queue(maxsize=settings.queue_max)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="uploader", daemon=True)
        self._lock = threading.Lock()
        self._uploaded = 0
        self._dropped = 0
        self._failed_attempts = 0

    # ── capture side (never blocks) ──────────────────────────────────────────

    def submit(self, item: CaptureItem) -> None:
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    with self._lock:
                        self._dropped += 1
                    log.warning(
                        "queue full - dropped oldest frame (dropped=%d)", self._dropped
                    )
                except queue.Empty:
                    pass  # raced with the uploader; try the put again

    # ── status (viewer /healthz) ─────────────────────────────────────────────

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def counters(self) -> dict[str, int]:
        with self._lock:
            return {
                "uploaded": self._uploaded,
                "dropped": self._dropped,
                "failed_attempts": self._failed_attempts,
            }

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread.start()

    def stop(self, drain_seconds: float = 10.0) -> None:
        """Give the queue a bounded chance to drain, then stop the thread."""
        deadline = time.monotonic() + drain_seconds
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.1)
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)

    # ── uploader thread ──────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("uploader started signer=%s", self._settings.upload_signer_url)
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self.process(item)
        log.info("uploader stopped %s", self.counters())

    def process(self, item: CaptureItem) -> bool:
        """Upload one item, retrying with backoff until success or stop."""
        backoff = BACKOFF_INITIAL_S
        attempt = 0
        while not self._stop.is_set():
            attempt += 1
            try:
                key = self._upload_once(item)
                with self._lock:
                    self._uploaded += 1
                log.info("uploaded key=%s attempt=%d depth=%d",
                         key, attempt, self._queue.qsize())
                return True
            except Exception as exc:
                with self._lock:
                    self._failed_attempts += 1
                log.warning(
                    "upload failed key=%s attempt=%d error=%s",
                    item.key, attempt, exc,
                )
                self._sleep(backoff)
                backoff = min(BACKOFF_CAP_S, backoff * 2)
        return False

    def _upload_once(self, item: CaptureItem) -> str:
        metadata = {
            "ulid": item.ulid,
            "device-id": self._settings.device_id,
            "captured-utc": item.captured_at.astimezone(UTC).isoformat(),
            "timezone": self._settings.timezone,
        }
        sign_request = urllib.request.Request(
            self._settings.upload_signer_url.rstrip("/") + "/sign",
            data=json.dumps(
                {
                    "token": self._settings.device_token,
                    "date": item.captured_at.strftime("%Y-%m-%d"),
                    "filename": f"{format_hhmmssfff(item.captured_at)}.jpg",
                    "content_type": CONTENT_TYPE,
                    "metadata": metadata,
                }
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self._urlopen(sign_request, timeout=SIGN_TIMEOUT_S) as response:
            signed = json.loads(response.read())

        headers = {"Content-Type": CONTENT_TYPE}
        headers.update({f"x-amz-meta-{k}": v for k, v in metadata.items()})
        put_request = urllib.request.Request(
            signed["url"], data=item.jpeg, method="PUT", headers=headers
        )
        with self._urlopen(put_request, timeout=PUT_TIMEOUT_S):
            pass
        return signed["key"]
