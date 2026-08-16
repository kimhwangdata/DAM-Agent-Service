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
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from agent.capture import CaptureItem, format_hhmmssfff
from agent.config import Settings
from agent.constants import (
    BACKOFF_CAP_S,
    BACKOFF_INITIAL_S,
    HEARTBEAT_MIN_INTERVAL_S,
    PUT_TIMEOUT_S,
    SIDECAR_META_KEYS,
    SIGN_TIMEOUT_S,
)
from shared.constants import CONTENT_TYPE_JPEG, CONTENT_TYPE_JSON, JPG_SUFFIX

log = logging.getLogger(__name__)



def build_sidecar(item: CaptureItem, status: dict[str, Any]) -> dict[str, Any]:
    """Per-frame hardware/capture log, uploaded as {hhmmssfff}.json next to
    the image (architecture §7): device basics + camera settings actually
    used (exposure/gain/lux) + hardware condition at capture time."""
    camera_meta: dict[str, Any] = {}
    for key in SIDECAR_META_KEYS:
        value = item.camera_metadata.get(key)
        if value is None:
            continue
        camera_meta[key] = list(value) if isinstance(value, tuple | list) else value
    return {
        "captured_at": item.captured_at.isoformat(),
        "ulid": item.ulid,
        "image_bytes": len(item.jpeg),
        "camera_meta": camera_meta,
        "status": status,
    }


class SkipUpload(Exception):
    """Server said the frame should be skipped (paused/unassigned) — §5."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        self._skipped = 0
        self._failed_attempts = 0
        # Learned from the signer's key on each upload (the assignment is
        # cloud-authoritative; the device env no longer carries a location).
        self.location_id: str | None = settings.location_id
        # Capture window learned from every /sign answer (operator-set in
        # the control plane); full day until the signer says otherwise.
        self.window: tuple[str, str] = ("00:00", "00:00")
        self._last_heartbeat_mono = 0.0
        # Set by Agent after construction; included in every /sign body so
        # the sign call doubles as the fleet heartbeat (design 02 §5).
        self.status_fn: Callable[[], dict[str, Any]] | None = None

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
                "skipped": self._skipped,
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
            except SkipUpload as skip:
                with self._lock:
                    self._skipped += 1
                log.info("skipped key=%s reason=%s", item.key, skip.reason)
                return True  # deliberate skip — not a failure, no retry
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

    def _sign(
        self, date: str, filename: str, metadata: dict, sidecar: bool = False
    ) -> dict:
        """POST /sign; raises SkipUpload for paused/unassigned answers."""
        body: dict[str, Any] = {
            "token": self._settings.device_token,
            "date": date,
            "filename": filename,
            "content_type": CONTENT_TYPE_JPEG,
            "metadata": metadata,
            "device_id": self._settings.device_id,
        }
        if sidecar:
            body["sidecar"] = True
        if self.status_fn is not None:
            body["status"] = self.status_fn()
        request = urllib.request.Request(
            self._settings.upload_signer_url.rstrip("/") + "/sign",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._urlopen(request, timeout=SIGN_TIMEOUT_S) as response:
                signed = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                try:
                    error = json.loads(exc.read()).get("error", "")
                except (json.JSONDecodeError, OSError):
                    error = ""
                if error == "unassigned":
                    raise SkipUpload("unassigned") from exc
            raise
        window = signed.get("window")
        if isinstance(window, dict) and window.get("start") and window.get("end"):
            self.window = (str(window["start"]), str(window["end"]))
        if signed.get("status") == "paused":
            raise SkipUpload("paused")
        return signed

    def _upload_once(self, item: CaptureItem) -> str:
        metadata = {
            "ulid": item.ulid,
            "device-id": self._settings.device_id,
            "captured-utc": item.captured_at.astimezone(UTC).isoformat(),
            "timezone": self._settings.timezone,
        }
        signed = self._sign(
            item.captured_at.strftime("%Y-%m-%d"),
            f"{format_hhmmssfff(item.captured_at)}{JPG_SUFFIX}",
            metadata,
            sidecar=True,
        )
        key_parts = str(signed.get("key", "")).split("/")
        if len(key_parts) >= 2 and key_parts[1]:
            self.location_id = key_parts[1]
        headers = {"Content-Type": CONTENT_TYPE_JPEG}
        headers.update({f"x-amz-meta-{k}": v for k, v in metadata.items()})
        put_request = urllib.request.Request(
            signed["url"], data=item.jpeg, method="PUT", headers=headers
        )
        with self._urlopen(put_request, timeout=PUT_TIMEOUT_S):
            pass
        self._upload_sidecar(item, signed)
        return signed["key"]

    def _upload_sidecar(self, item: CaptureItem, signed: dict) -> None:
        """Best-effort hardware/capture log next to the frame (§7 sidecar).
        Never fails the frame — the image is already safely uploaded."""
        sidecar_url = signed.get("sidecar_url")
        if not sidecar_url:
            return
        try:
            status = self.status_fn() if self.status_fn is not None else {}
            payload = json.dumps(
                build_sidecar(item, status), default=str
            ).encode("utf-8")
            request = urllib.request.Request(
                sidecar_url, data=payload, method="PUT",
                headers={"Content-Type": CONTENT_TYPE_JSON},
            )
            with self._urlopen(request, timeout=PUT_TIMEOUT_S):
                pass
        except Exception as exc:
            log.warning(
                "sidecar upload failed key=%s error=%s",
                signed.get("sidecar_key"), exc,
            )

    def send_heartbeat(self) -> None:
        """Status-only /sign (URL unused) — keeps the manager informed
        while the camera rests (thermal pause / capture-window idle).
        Rate-limited so rest-state loops can call it freely. Never
        raises."""
        now = time.monotonic()
        if now - self._last_heartbeat_mono < HEARTBEAT_MIN_INTERVAL_S:
            return
        self._last_heartbeat_mono = now
        now = datetime.now(ZoneInfo(self._settings.timezone))
        try:
            self._sign(
                now.strftime("%Y-%m-%d"),
                f"{format_hhmmssfff(now)}{JPG_SUFFIX}",
                {"device-id": self._settings.device_id},
            )
        except SkipUpload:
            pass  # paused/unassigned — status was still recorded server-side
        except Exception as exc:
            log.warning("heartbeat failed error=%s", exc)
