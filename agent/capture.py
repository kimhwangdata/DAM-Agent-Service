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
from agent.constants import MIN_INTERVAL_S
from shared.constants import (
    CAPTURE_DURATION_SECONDS,
    FRAME_PER_MINUTE,
    JPG_SUFFIX,
)

log = logging.getLogger(__name__)


def _hhmm_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def window_seconds(start: str, end: str) -> int:
    """Capture-window length; start == end means the full day (§5.3)."""
    s, e = _hhmm_minutes(start), _hhmm_minutes(end)
    if s == e:
        return CAPTURE_DURATION_SECONDS
    minutes = e - s if e > s else 24 * 60 - (s - e)
    return minutes * 60


def in_window(now: datetime, start: str, end: str) -> bool:
    """Is device-local ``now`` inside the capture window? Full-day windows
    are always inside; start > end crosses midnight."""
    s, e = _hhmm_minutes(start), _hhmm_minutes(end)
    if s == e:
        return True
    t = now.hour * 60 + now.minute
    if s < e:
        return s <= t < e
    return t >= s or t < e


def capture_interval_s(window_s: int, video_minutes: int) -> int:
    """Seconds between captures so the window still fills VIDEO_MINUTES of
    30 fps video — a 12 h window at 1 min video means one frame per 24 s."""
    return max(MIN_INTERVAL_S, window_s // (FRAME_PER_MINUTE * video_minutes))


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
    return f"{image_prefix}{location_id}/{day}/{format_hhmmssfff(ts)}{JPG_SUFFIX}"


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
        interval_fn: Callable[[], int] | None = None,
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
        # dynamic cadence (capture-window aware); None = fixed settings value
        self._interval_fn = interval_fn
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
            if self._interval_fn is not None:
                new_interval = self._interval_fn()
                if new_interval != interval:
                    log.info("capture interval %ds -> %ds (window change)",
                             interval, new_interval)
                    interval = new_interval
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
            # preview_active first: without a viewer the gate must not run
            # here (its rest-state heartbeats would fire once per slice)
            if self._preview_active() and (self._gate is None or self._gate()):
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
