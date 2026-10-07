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


def match_to_mic(mic: Path, clip: Path) -> dict:
    """Lock a clip to the DJI mic by the clap, even when the mic has several slates.

    `mic_start` is the mic time that lines up with the start of the clip.
    """
    if not has_audio(mic) or not has_audio(clip):
        return {"ok": False, "reason": "No audio to lock this clip to the mic."}
    rate = 8000
    clip_seconds = min(probe_duration(clip) or 20.0, 30.0)
    mic_seconds = probe_duration(mic) or 20.0
    clip_pcm = _pcm(clip, clip_seconds, rate)
    mic_pcm = _pcm(mic, mic_seconds, rate)
    if clip_pcm.size < rate or mic_pcm.size < rate:
        return {"ok": False, "reason": "Not enough audio to find a clap."}
    search = clip_pcm[: min(clip_pcm.size, 20 * rate)]
    clip_peak = int(np.argmax(np.abs(search)))
    if _peak_score(search, clip_peak) < 8:
        return {"ok": False, "reason": "No clap on this clip, so it stays in the sequence."}
    mic_peaks = _peaks(mic_pcm, rate)
    if not mic_peaks:
        return {"ok": False, "reason": "No clap on the mic."}
    window = 2 * rate
    skip = int(0.05 * rate)
    clip_win = clip_pcm[clip_peak + skip:clip_peak + skip + window]
    scored = []
    for peak in mic_peaks:
        mic_win = mic_pcm[peak + skip:peak + skip + window]
        length = min(clip_win.size, mic_win.size)
        if length < rate // 2:
            continue
        scored.append(( _corr(clip_win[:length], mic_win[:length]), peak))
    if not scored:
        return {"ok": False, "reason": "The mic claps are too late in the file to compare."}
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_peak = scored[0]
    if len(scored) == 1:
        locked = True
    else:
        locked = best_score >= 0.45 and best_score - scored[1][0] >= 0.08
    if not locked:
        return {
            "ok": False,
            "confidence": round(best_score, 2),
            "reason": "This clip does not lock to one mic clap, so it stays in the sequence.",
        }
    return {
        "ok": True,
        "mic_start": round((best_peak - clip_peak) / rate, 3),
        "clip_clap_at": round(clip_peak / rate, 3),
        "mic_clap_at": round(best_peak / rate, 3),
        "confidence": round(best_score, 2),
        "reason": None,
    }


def _peaks(samples: np.ndarray, rate: int) -> list[int]:
    magnitude = np.abs(samples.astype(np.float32))
    if magnitude.size == 0:
        return []
    median = float(np.median(magnitude)) + 1.0
    threshold = max(8.0 * median, 8000.0)
    hits = np.flatnonzero(magnitude >= threshold)
    if hits.size == 0:
        return []
    gap = int(0.45 * rate)
    peaks = []
    start = 0
    for index in range(1, hits.size + 1):
        if index == hits.size or hits[index] - hits[index - 1] > gap:
            cluster = hits[start:index]
            peaks.append(int(cluster[int(np.argmax(magnitude[cluster]))]))
            start = index
    return peaks


def _corr(left: np.ndarray, right: np.ndarray) -> float:
    a = left.astype(np.float32)
    b = right.astype(np.float32)
    a = a - float(a.mean())
    b = b - float(b.mean())
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-3:
        return 0.0
    return float(np.dot(a, b) / denom)


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
