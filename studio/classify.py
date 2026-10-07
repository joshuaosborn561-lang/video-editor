"""Tell b-roll from the Cap sequence, then lay the b-roll onto that sequence.

Cap clips are the sequence the user orders: intro, screen, or outro.
A video that shares a clap with the DJI mic is b-roll. The mic is the audio.
"""

from __future__ import annotations

import re
from pathlib import Path

_INTRO = re.compile(r"\bintro\b", re.I)
_OUTRO = re.compile(r"\boutro\b|\bclosing\b", re.I)


def role_from_title(title: str) -> str:
    text = title or ""
    if _INTRO.search(text):
        return "intro"
    if _OUTRO.search(text):
        return "outro"
    return "screen"


def suggest_role(title: str, match: dict | None) -> str:
    """A clap that locks to the mic means b-roll. Otherwise the name decides."""
    if match and match.get("ok"):
        return "broll"
    named = role_from_title(title)
    return named


def plan_sequence(caps: list[dict], brolls: list[dict]) -> list[dict]:
    """Place ordered Cap clips, and drop b-roll in where the mic says it belongs.

    Intros play first and outros last. B-roll that locks to the mic plays in
    mic order, and a screen that locks to the same mic cuts in over it. A
    screen or b-roll clip with no clap stays in order between those.
    """
    intros = [clip for clip in caps if clip.get("role") == "intro"]
    outros = [clip for clip in caps if clip.get("role") == "outro"]
    screens = [clip for clip in caps if clip.get("role") not in {"intro", "outro"}]
    locked_faces = sorted(
        (_without_clap(clip) for clip in brolls if clip.get("mic_start") is not None),
        key=lambda clip: float(clip["mic_start"]),
    )
    loose_faces = [_without_clap(clip) for clip in brolls if clip.get("mic_start") is None]
    locked_screens = sorted(
        (_without_clap(clip) for clip in screens if clip.get("mic_start") is not None),
        key=lambda clip: float(clip["mic_start"]),
    )
    loose_screens = [_without_clap(clip) for clip in screens if clip.get("mic_start") is None]

    middle = _on_clock(locked_faces, locked_screens)
    middle.extend(_zip_loose(loose_faces, loose_screens))
    return [_bookend(clip) for clip in intros] + middle + [_bookend(clip) for clip in outros]


def _without_clap(item: dict) -> dict:
    shifted = dict(item)
    clap = item.get("clip_clap_at")
    duration = float(item.get("duration") or 0)
    src = float(item.get("src") or 0)
    if clap is None:
        shifted["src"] = src
        shifted["duration"] = max(0.0, duration - src) if src else duration
        return shifted
    cut = float(clap) + 0.18
    if cut >= duration - 0.2:
        shifted["src"] = src
        shifted["duration"] = duration
        return shifted
    shifted["src"] = cut
    shifted["duration"] = duration - cut
    if shifted.get("mic_start") is not None:
        shifted["mic_start"] = float(shifted["mic_start"]) + (cut - src)
    return shifted


def _on_clock(faces: list[dict], screens: list[dict]) -> list[dict]:
    pieces: list[dict] = []
    for face in faces:
        _punch(pieces, _clock_piece(face, "face"))
    for screen in screens:
        _punch(pieces, _clock_piece(screen, "screen"))
    pieces.sort(key=lambda piece: float(piece["start"]))
    return [_public(piece) for piece in pieces if piece["duration"] >= 0.2]


def _clock_piece(item: dict, kind: str) -> dict:
    return {
        "kind": kind,
        "file": item.get("file") or "",
        "title": item.get("title") or "",
        "src": float(item.get("src") or 0),
        "duration": float(item.get("duration") or 0),
        "audio": "mic",
        "mic_at": float(item.get("mic_start") or 0),
        "start": float(item.get("mic_start") or 0),
    }


def _punch(pieces: list[dict], incoming: dict) -> None:
    start = incoming["start"]
    end = start + incoming["duration"]
    if incoming["duration"] < 0.2:
        return
    kept: list[dict] = []
    for piece in pieces:
        piece_start = piece["start"]
        piece_end = piece_start + piece["duration"]
        if piece_end <= start or piece_start >= end:
            kept.append(piece)
            continue
        if piece_start < start:
            kept.append({**piece, "duration": start - piece_start})
        if piece_end > end:
            delta = end - piece_start
            kept.append({
                **piece,
                "start": end,
                "src": piece["src"] + delta,
                "duration": piece_end - end,
                "mic_at": piece["mic_at"] + delta,
            })
    kept.append(incoming)
    pieces[:] = [piece for piece in kept if piece["duration"] >= 0.2]


def _zip_loose(faces: list[dict], screens: list[dict]) -> list[dict]:
    pieces = []
    face_index = 0
    screen_index = 0
    while face_index < len(faces) or screen_index < len(screens):
        if face_index < len(faces):
            pieces.append(_loose_piece(faces[face_index], "face"))
            face_index += 1
        if screen_index < len(screens):
            pieces.append(_loose_piece(screens[screen_index], "screen"))
            screen_index += 1
    return [piece for piece in pieces if piece["duration"] >= 0.2]


def _loose_piece(item: dict, kind: str) -> dict:
    return {
        "kind": kind,
        "file": item.get("file") or "",
        "title": item.get("title") or "",
        "src": float(item.get("src") or 0),
        "duration": float(item.get("duration") or 0),
        "audio": "clip",
        "mic_at": None,
    }


def _bookend(item: dict) -> dict:
    trimmed = _without_clap(item)
    return {
        "kind": item.get("role") or "screen",
        "file": item.get("file") or "",
        "title": item.get("title") or "",
        "src": float(trimmed.get("src") or 0),
        "duration": float(trimmed.get("duration") or 0),
        "audio": "clip",
        "mic_at": None,
    }


def _public(piece: dict) -> dict:
    return {
        "kind": piece["kind"],
        "file": piece["file"],
        "title": piece["title"],
        "src": round(float(piece["src"]), 3),
        "duration": round(float(piece["duration"]), 3),
        "audio": piece["audio"],
        "mic_at": None if piece["mic_at"] is None else round(float(piece["mic_at"]), 3),
    }


def describe(caps: list[dict], brolls: list[dict]) -> str:
    locked = [clip for clip in brolls if clip.get("mic_start") is not None]
    loose = [clip for clip in brolls if clip.get("mic_start") is None]
    screens = [clip for clip in caps if clip.get("role") == "screen" and clip.get("mic_start") is not None]
    parts = []
    if locked:
        parts.append(f"{len(locked)} b-roll clip{' locks' if len(locked) == 1 else 's lock'} to the mic.")
    if loose:
        parts.append(f"{len(loose)} b-roll clip{'s' if len(loose) != 1 else ''} did not lock and stayed in the order you set.")
    if screens:
        parts.append(f"{len(screens)} Cap clip{' shares' if len(screens) == 1 else 's share'} a clap with the mic and cut in there.")
    return " ".join(parts)
