"""Capture-cycle math — pure functions, no AWS (design 03 §3, 02 §5.3).

A cycle is one day's video window in the device's local timezone:

- ``start == end`` (default "00:00"/"00:00"): the whole local day D;
  the cycle ends at D+1 00:00 (the legacy midnight rule).
- ``start < end``: frames of day D between start and end; ends at D end.
- ``start > end`` (crosses midnight): the cycle **labeled** D spans
  D start -> D+1 end; it ends at D+1 end.

Filenames are ``hhmmssfff`` (9 digits), so window filtering is plain
lexicographic comparison on basenames.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

END_OF_DAY = "999999999"  # sorts after any hhmmssfff


def hhmm_to_prefix(hhmm: str) -> str:
    """'06:30' -> '063000000' (comparable against hhmmssfff basenames)."""
    if not HHMM_RE.match(hhmm):
        raise ValueError(f"expected HH:MM, got {hhmm!r}")
    return hhmm.replace(":", "") + "00000"


@dataclass(frozen=True)
class FrameRange:
    """Frames of one day-folder: lo <= hhmmssfff < hi (lexicographic)."""

    day: str  # YYYY-MM-DD folder
    lo: str
    hi: str


def latest_completed_cycle(now_local: datetime, start: str, end: str) -> str:
    """Label (YYYY-MM-DD) of the most recent COMPLETED cycle at now_local."""
    today = now_local.date()
    now_hhmm = now_local.strftime("%H:%M")
    if start == end:
        # ends at next local midnight -> yesterday's cycle is the done one
        return (today - timedelta(days=1)).isoformat()
    if start < end:
        # ends the same day at `end`
        done = today if now_hhmm >= end else today - timedelta(days=1)
        return done.isoformat()
    # start > end: the cycle ending today at `end` is labeled yesterday
    done = today - timedelta(days=1) if now_hhmm >= end else today - timedelta(days=2)
    return done.isoformat()


def frame_ranges(cycle_date: str, start: str, end: str) -> list[FrameRange]:
    """Day folder(s) + basename bounds for the cycle labeled cycle_date."""
    day = date.fromisoformat(cycle_date)
    next_day = (day + timedelta(days=1)).isoformat()
    if start == end:
        return [FrameRange(cycle_date, "000000000", END_OF_DAY)]
    if start < end:
        return [FrameRange(cycle_date, hhmm_to_prefix(start), hhmm_to_prefix(end))]
    return [
        FrameRange(cycle_date, hhmm_to_prefix(start), END_OF_DAY),
        FrameRange(next_day, "000000000", hhmm_to_prefix(end)),
    ]


def in_range(basename: str, frame_range: FrameRange) -> bool:
    """Is an hhmmssfff.jpg basename inside the range?"""
    stem = basename.removesuffix(".jpg")
    return frame_range.lo <= stem < frame_range.hi
