"""Agent entrypoint — wiring, signals, logging (design §8).

Threads: main (capture loop), uploader, viewer. SIGTERM/SIGINT set a stop
event that both ends the loop and interrupts its sleep (the loop's sleep is
``Event.wait``), then the camera stops, the viewer closes, and the uploader
gets a bounded drain. Anything still queued after the drain is lost by
design (01-agent.md §1).
"""

from __future__ import annotations

import logging
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from agent import __version__
from agent.camera import CameraSource, FakeCamera, Picamera2Camera
from agent.capture import CaptureItem, CaptureLoop
from agent.config import Settings, load_settings
from agent.thermal import ThermalMonitor, ThermalStatus, read_pi_model
from agent.uploader import Uploader
from agent.viewer import FrameStore, Viewer

log = logging.getLogger(__name__)

UPLOADER_DRAIN_S = 10.0


def build_camera(settings: Settings) -> CameraSource:
    tz = ZoneInfo(settings.timezone)
    if settings.stage == "test":
        return FakeCamera(tz=tz, size=settings.capture_size)
    return Picamera2Camera(
        tz=tz,
        size=settings.capture_size,
        # manual night exposure must fit inside the frame duration limit
        max_exposure_ms=max(settings.max_exposure_ms, settings.night_exposure_ms),
        tuning_file=settings.tuning_file,
        night_exposure_ms=settings.night_exposure_ms,
        night_gain=settings.night_gain,
        raw_size=settings.raw_size,
    )


class Agent:
    def __init__(
        self,
        settings: Settings,
        *,
        camera: CameraSource | None = None,
        urlopen: Callable | None = None,
        thermal: ThermalMonitor | None = None,
        poweroff: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.camera = camera if camera is not None else build_camera(settings)
        self.frames = FrameStore()
        self._stop_event = threading.Event()
        uploader_kwargs = {"urlopen": urlopen} if urlopen is not None else {}
        self.uploader = Uploader(settings, **uploader_kwargs)
        self.uploader.status_fn = self.status
        self.thermal = thermal if thermal is not None else ThermalMonitor(settings)
        self._thermal_status = ThermalStatus("ok", None, None, False)
        self._poweroff = poweroff if poweroff is not None else _sudo_poweroff
        self._pi_model = read_pi_model()
        self.loop = CaptureLoop(
            self.camera,
            settings,
            self._sink,
            sleep=self._stop_event.wait,
            gate=self._thermal_gate,
            # late-bound: self.viewer is created a few lines below
            preview_active=lambda: bool(
                self.viewer and self.viewer.active_clients > 0
            ),
            preview_publish=self.frames.publish,
        )
        self.viewer = (
            Viewer(settings.viewer_port, self.frames, self.status)
            if settings.viewer_port
            else None
        )
        self._started_monotonic = time.monotonic()

    def _thermal_gate(self) -> bool:
        """Per-interval thermal check (design 02 §5.2). False = skip capture."""
        status = self.thermal.check()
        previous = self._thermal_status
        self._thermal_status = status
        if status.state != previous.state:
            log.warning(
                "thermal state %s -> %s temp=%s", previous.state, status.state,
                status.temp_c,
            )
        if status.should_shutdown:
            log.error("thermal shutdown temp=%s - powering off", status.temp_c)
            self.uploader.send_heartbeat()  # final report: event below
            self.request_stop()
            self._poweroff()
            return False
        if status.state == "paused":
            # keep the manager informed while the camera rests
            self.uploader.send_heartbeat()
            return False
        return True

    def _sink(self, item: CaptureItem) -> None:
        self.frames.publish(item.jpeg, item.captured_at)
        self.uploader.submit(item)

    def status(self) -> dict[str, Any]:
        """Config + counters for /healthz and the /sign heartbeat (§5) —
        no secrets."""
        thermal = self._thermal_status
        status: dict[str, Any] = {
            "stage": self.settings.stage,
            "device_id": self.settings.device_id,
            "location_id": self.uploader.location_id,
            "timezone": self.settings.timezone,
            "interval_s": self.settings.interval_s,
            "capture_size": f"{self.settings.capture_size[0]},"
                            f"{self.settings.capture_size[1]}",
            "agent_version": __version__,
            "queue_depth": self.uploader.queue_depth,
            "uptime_s": int(time.monotonic() - self._started_monotonic),
            "thermal_state": thermal.state,
            **self.uploader.counters(),
        }
        if thermal.temp_c is not None:
            status["temp_c"] = round(thermal.temp_c, 1)
        if thermal.throttled is not None:
            status["throttled"] = thermal.throttled
        if self._pi_model:
            status["pi_model"] = self._pi_model
        model = getattr(self.camera, "model", None)
        if model:
            status["camera"] = model
        if getattr(self.camera, "is_night", False):
            status["night_mode"] = True
        if thermal.should_shutdown:
            status["event"] = "thermal-shutdown"
        return status

    def request_stop(self, *_args: Any) -> None:
        log.info("stop requested")
        self.loop.stop()
        self._stop_event.set()

    def run(self) -> None:
        log.info("dam-agent starting %s", self.status())
        self.camera.start()
        self.uploader.start()
        if self.viewer is not None:
            self.viewer.start()
        try:
            self.loop.run()
        finally:
            self.camera.stop()
            if self.viewer is not None:
                self.viewer.stop()
            self.uploader.stop(drain_seconds=UPLOADER_DRAIN_S)
            log.info("dam-agent stopped %s", self.uploader.counters())


def _sudo_poweroff() -> None:
    """OS shutdown for sustained over-temperature (sudoers rule from
    provision-pi.sh). Never raises — failing to power off must not crash
    the agent."""
    try:
        subprocess.run(["sudo", "-n", "/sbin/poweroff"], timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("poweroff failed: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    agent = Agent(load_settings())
    signal.signal(signal.SIGTERM, agent.request_stop)
    signal.signal(signal.SIGINT, agent.request_stop)
    agent.run()


if __name__ == "__main__":
    main()
