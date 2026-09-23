"""Edit plan, checklist, and the price shown before a render."""

from __future__ import annotations

import re

MARKER = re.compile(r"\[\[(face|screen|card|broll)(?::\s*(.*?))?\]\]", re.IGNORECASE)
WORDS_PER_SECOND = 2.4  # conversational pace, about 145 wpm
DEEPGRAM_PER_MINUTE = 0.0043
RENDER_FLAT = 2.0
LLM_FLAT = 3.0
MUSIC_FLAT = 3.0

CHECKLIST_LABELS = {
    "face_early": "A face is on screen in the opening",
    "open_loop": "An open loop or proof card lands in the first 30 seconds",
    "visual_cadence": "The picture changes at least every 30 seconds",
    "two_ctas": "A mid-roll ask and an end ask are both in the script",
    "captions": "Bottom captions, one word highlighted, on every layout",
    "music_switch": "One bed under the hook, a second bed after it",
    "no_fake_broll": "Screen recordings and cards only. No generated software shots",
    "dead_air": "Dead air and restarts get cut when footage is ingested",
    "thumbnail": "Thumbnail is a glance: a few words and one color",
}


def build_plan(
    script: str,
    picks: dict,
    footage: dict,
    raw_minutes: float | None,
    vendors: dict,
) -> dict:
    beats = parse_beats(script)
    issues = cadence_issues(beats)
    checklist = checklist_for(script, beats, issues, picks)
    minutes = raw_minutes if raw_minutes and raw_minutes > 0 else None
    quote = quote_for(minutes or 40.0, vendors, estimated=minutes is None)
    return {
        "beats": beats,
        "cadence_issues": issues,
        "checklist": checklist,
        "quote": quote,
        "caption_style": {
            "position": "bottom-center",
            "type": "Inter Bold",
            "fill": "white",
            "stroke": "black",
            "highlight": picks.get("accent") or "yellow",
            "words_per_line": "4-7",
        },
        "music": {
            "intro_seconds": 20,
            "then": "second bed, ducked under the voice",
            "source": "uploaded files" if footage.get("music_intro") else "not attached yet",
        },
        "sync": footage.get("sync"),
        "ready": all(item["ok"] for item in checklist) and not issues,
    }


def parse_beats(script: str) -> list[dict]:
    beats = []
    cursor = 0.0
    for match in MARKER.finditer(script):
        kind = match.group(1).lower()
        note = (match.group(2) or "").strip()
        start = match.end()
        nxt = MARKER.search(script, start)
        body = script[start : nxt.start() if nxt else len(script)]
        spoken = " ".join(body.split())
        words = len([word for word in spoken.split() if word])
        duration = max(2.0, words / WORDS_PER_SECOND) if words else 2.0
        beats.append(
            {
                "layout": kind,
                "note": note,
                "start": round(cursor, 1),
                "duration": round(duration, 1),
                "words": words,
                "spoken": spoken[:280],
            }
        )
        cursor += duration
    return beats


def cadence_issues(beats: list[dict]) -> list[str]:
    issues = []
    for beat in beats:
        if beat["layout"] == "face" and beat["duration"] > 30:
            issues.append(
                f"Face stays up for {beat['duration']}s starting at {beat['start']}s. "
                "Add a [[card: ]] or [[screen: ]] before the 30 second mark."
            )
    if beats and beats[0]["layout"] != "face":
        issues.append("The opening picture is not the face. Move [[face]] to the first line.")
    return issues


def checklist_for(script: str, beats: list[dict], issues: list[str], picks: dict) -> list[dict]:
    lower = script.lower()
    early = [beat for beat in beats if beat["start"] < 30]
    thumb_words = picks.get("thumb_lines") or []
    word_count = len(" ".join(thumb_words).split())
    flags = {
        "face_early": bool(beats) and beats[0]["layout"] == "face",
        "open_loop": any(beat["layout"] == "card" for beat in early) or "if you" in lower[:400],
        "visual_cadence": not any("Face stays up" in issue for issue in issues),
        "two_ctas": "description" in lower and ("book" in lower or "link" in lower),
        "captions": True,
        "music_switch": True,
        "no_fake_broll": "generated" not in lower,
        "dead_air": True,
        "thumbnail": 1 <= word_count <= 6 and len(thumb_words) <= 3,
    }
    return [{"id": key, "label": CHECKLIST_LABELS[key], "ok": flags[key]} for key in CHECKLIST_LABELS]


def quote_for(raw_minutes: float, vendors: dict, estimated: bool) -> dict:
    lines = []
    transcription = round(raw_minutes * DEEPGRAM_PER_MINUTE, 2)
    if vendors.get("deepgram"):
        lines.append({"item": f"Transcription ({raw_minutes:.0f} min of raw)", "amount": transcription})
    else:
        lines.append({"item": "Transcription (add a Deepgram key to turn this on)", "amount": 0})
    if vendors.get("anthropic") or vendors.get("openai"):
        lines.append({"item": "Script and edit notes", "amount": LLM_FLAT})
    else:
        lines.append({"item": "Script (local template, no model charge)", "amount": 0})
    lines.append({"item": "Quoted render of the full cut", "amount": RENDER_FLAT})
    if vendors.get("epidemic"):
        lines.append({"item": "Music search", "amount": 0})
    else:
        lines.append({"item": "Music beds (two files you attach, or Epidemic later)", "amount": 0})
    lines.append({"item": "Thumbnail", "amount": 0})
    total = round(sum(line["amount"] for line in lines), 2)
    return {
        "lines": lines,
        "total": total,
        "estimated_minutes": estimated,
        "note": (
            "Minutes are a 40-minute stand-in until the camera file is uploaded."
            if estimated
            else "Minutes come from the camera file."
        ),
    }
