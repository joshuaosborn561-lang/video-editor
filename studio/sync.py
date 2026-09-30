"""Clap alignment between the camera scratch track and the DJI Mic."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np


def probe_duration(path: Path) -> float | None:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def has_audio(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=False,
    )
    return "audio" in result.stdout


def clap_offset(reference: Path, other: Path, seconds: float = 20.0) -> dict:
    """Return how many seconds to shift `other` so its clap matches `reference`."""
    if not has_audio(reference) or not has_audio(other):
        return {
            "ok": False,
            "reason": "One of the files has no audio, so the clap cannot lock them. Treat it as an insert.",
        }
    rate = 8000
    left = _pcm(reference, seconds, rate)
    right = _pcm(other, seconds, rate)
    if left.size < rate or right.size < rate:
        return {"ok": False, "reason": "Not enough audio in the first 20 seconds to find a clap."}
    left_peak = int(np.argmax(np.abs(left)))
    right_peak = int(np.argmax(np.abs(right)))
    left_score = _peak_score(left, left_peak)
    right_score = _peak_score(right, right_peak)
    offset = (right_peak - left_peak) / rate
    confident = left_score >= 8 and right_score >= 8
    return {
        "ok": confident,
        "offset_seconds": round(offset, 3),
        "reference_clap_at": round(left_peak / rate, 3),
        "other_clap_at": round(right_peak / rate, 3),
        "confidence": round(min(left_score, right_score), 1),
        "reason": None if confident else "The loudest moment in the first 20 seconds is not sharp enough to call a clap. Re-record the slate or align by hand.",
    }


def _peak_score(samples: np.ndarray, index: int) -> float:
    peak = abs(float(samples[index]))
    median = float(np.median(np.abs(samples))) + 1.0
    return peak / median


def _pcm(path: Path, seconds: float, rate: int) -> np.ndarray:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-t", str(seconds),
            "-ac", "1", "-ar", str(rate), "-f", "s16le", "-",
        ],
        capture_output=True, check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return np.array([], dtype=np.int16)
    return np.frombuffer(result.stdout, dtype=np.int16)
