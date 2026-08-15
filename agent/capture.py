"""Capture loop — pacing, timestamps, and S3 key building (design §3–§4).

Drift-compensated cadence exactly like the legacy ``capture-24h.py``: note
the start of each iteration, capture, then sleep the remainder of the
interval. Day rollover needs no special handling — the key's date folder
simply follows the capture-time local date.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ulid import ULID

from agent.camera import CameraSource
from agent.config import PREVIEW_INTERVAL_S, Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureItem:
    """One captured frame, ready for upload — lives only in memory."""

    jpeg: bytes
    captured_at: datetime  # aware, device-local
    ulid: str
    key: str  # full S3 object key
    camera_metadata: dict[str, Any]


def format_hhmmssfff(ts: datetime) -> str:
    """Time-of-day filename part: %H%M%S%f truncated to milliseconds."""
    return ts.strftime("%H%M%S%f")[:-3]


def build_key(image_prefix: str, location_id: str, ts: datetime) -> str:
    """images/{location_id}/{YYYY-MM-DD}/{hhmmssfff}.jpg (architecture §7)."""
    day = ts.strftime("%Y-%m-%d")
    return f"{image_prefix}{location_id}/{day}/{format_hhmmssfff(ts)}.jpg"


class CaptureLoop:
    """Owns the cadence; hands each frame to a non-blocking sink."""

    def __init__(
        self,
        camera: CameraSource,
        settings: Settings,
        sink: Callable[[CaptureItem], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        gate: Callable[[], bool] | None = None,
        preview_active: Callable[[], bool] | None = None,
        preview_publish: Callable[[bytes, datetime], None] | None = None,
    ) -> None:
        self._camera = camera
        self._settings = settings
        self._sink = sink
        self._clock = clock
        self._sleep = sleep
        self._gate = gate  # returns False to skip this interval (thermal pause)
        # Live-view boost (design §6): while preview_active() is True the
        # wait between scheduled captures is filled with preview-only
        # captures published straight to the viewer — never to the sink,
        # so the upload cadence and the daily video are unaffected.
        self._preview_active = preview_active
        self._preview_publish = preview_publish
        self._running = False

    def capture_once(self) -> CaptureItem:
        jpeg, captured_at, metadata = self._camera.capture_jpeg()
        item = CaptureItem(
            jpeg=jpeg,
            captured_at=captured_at,
            ulid=str(ULID()),
            key=build_key(
                self._settings.s3_image_prefix,
                # display-only: the signer's assignment is authoritative (§6)
                self._settings.location_id or "unassigned",
                captured_at,
            ),
            camera_metadata=metadata,
        )
        self._sink(item)  # sink must never block (uploader guarantees this)
        return item

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        self._running = True
        interval = self._settings.interval_s
        log.info("capture loop started interval_s=%d", interval)
        while self._running:
            started = self._clock()
            try:
                if self._gate is None or self._gate():
                    item = self.capture_once()
                    duration = self._clock() - started
                    log.info("capture key=%s dur=%.3fs", item.key, duration)
                else:
                    duration = self._clock() - started
            except Exception:
                duration = self._clock() - started
                log.exception("capture failed dur=%.3fs", duration)
            if self._preview_active is None or self._preview_publish is None:
                self._sleep(max(0.0, interval - duration))
            else:
                self._wait_with_preview(started, interval)
        log.info("capture loop stopped")

    def _wait_with_preview(self, started: float, interval: float) -> None:
        """Sleep out the interval in slices, taking viewer-only preview
        captures while a stream client is connected (and not thermally
        paused)."""
        while self._running:
            remaining = interval - (self._clock() - started)
            if remaining <= 0:
                return
            gate_open = self._gate is None or self._gate()
            if gate_open and self._preview_active():
                t0 = self._clock()
                try:
                    jpeg, captured_at, _ = self._camera.capture_jpeg()
                    self._preview_publish(jpeg, captured_at)
                except Exception:
                    log.exception("preview capture failed")
                spent = self._clock() - t0
                remaining = interval - (self._clock() - started)
                self._sleep(max(0.0, min(PREVIEW_INTERVAL_S - spent, remaining)))
            else:
                self._sleep(min(PREVIEW_INTERVAL_S, remaining))
