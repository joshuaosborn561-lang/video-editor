"""Full cut. Clap sync, dead air, retakes, captions, cards, screen, music."""

from __future__ import annotations

import re
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from studio import plan, sync, transcribe

FONT = Path(__file__).resolve().parent / "fonts" / "Inter-Bold.ttf"
WIDTH = 1920
HEIGHT = 1080
YELLOW = "00E1FF4A"  # ASS is &HAABBGGRR; stored here as BBGGRR for the override
RED = "00303BFF"
STOP = {"a", "an", "the", "of", "to", "and", "or", "for", "in", "on", "at", "is", "it", "we", "you", "your", "this", "that", "with", "from"}


class CutError(RuntimeError):
    pass


def render_cut(folder: Path, script: str, picks: dict, footage: dict, dest: Path) -> dict:
    camera_name = footage.get("camera")
    if not camera_name:
        raise CutError("attach a camera file before rendering")
    camera = folder / camera_name
    if not camera.is_file():
        raise CutError("camera file is missing")
    duration = sync.probe_duration(camera) or 0
    if duration < 0.3:
        raise CutError("camera file is too short to cut")

    mic = _existing(folder, footage.get("mic"))
    screen = _existing(folder, footage.get("screen"))
    music_intro = _existing(folder, footage.get("music_intro"))
    music_body = _existing(folder, footage.get("music_body"))
    voice = mic or camera
    mic_offset = _offset(footage, "camera_mic") if mic else 0.0
    screen_offset = _offset(footage, "camera_screen") if screen else None

    warning = None
    transcript = None
    try:
        transcript = transcribe.transcribe(voice)
    except Exception as exc:
        warning = f"Deepgram did not answer ({exc}). Captions follow the script."
    words = list((transcript or {}).get("words") or [])

    voice_duration = sync.probe_duration(voice) or duration
    silence = _silence_ranges(voice, voice_duration)
    claps = _clap_times(voice, voice_duration)
    voice_spans = kept_spans(voice_duration, silence, claps, words)
    camera_spans = _shift_spans(voice_spans, -mic_offset, duration)
    if not camera_spans:
        camera_spans = [(0.0, duration)]
        voice_spans = [(max(0.0, mic_offset), max(0.05, mic_offset + duration))]

    kept = sum(end - start for start, end in camera_spans)
    highlight = (picks.get("highlight") or "").strip()
    accent = RED if picks.get("accent") == "red" else YELLOW
    timed = _timed_words(words, voice_spans) if words else []
    beats = _place_beats(plan.parse_beats(script), kept, timed)
    if not beats:
        hook = ((picks.get("hook") or {}).get("text") or " ").strip()
        beats = [{"layout": "face", "note": "", "spoken": hook, "words": len(hook.split()), "out_start": 0.0, "out_end": kept}]
    captions = _captions(beats, timed, highlight)
    if not captions:
        captions = [{"start": 0.0, "end": kept, "words": [" "], "highlight": ""}]

    work = dest.parent / "_cut"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        segments = _slices(beats, camera_spans)
        paths = []
        screen_cursor = 0.0
        for index, segment in enumerate(segments):
            label = work / f"label-{index}.txt"
            _write_label(label, segment["note"])
            path = work / f"seg-{index:03d}.mp4"
            screen_cursor = _render_segment(
                path, segment, camera, mic, mic_offset, screen, screen_offset, screen_cursor, label,
            )
            paths.append(path)
        master = work / "master.mp4"
        _concat(paths, master, work)
        ass = work / "captions.ass"
        _write_ass(ass, captions, accent)
        switch = beats[0]["out_end"] if beats else min(20.0, kept)
        music = _music_bed(work, music_intro, music_body, switch, kept)
        ticks = _ticks(work / "ticks.wav", kept, [beat["out_start"] for beat in beats if beat["layout"] == "card"])
        _finish(master, ass, music, ticks, dest)
    except CutError:
        raise
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace")[-500:]
        raise CutError(detail or "ffmpeg failed") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return {
        "captions": "deepgram" if timed else "script",
        "kept_seconds": round(kept, 2),
        "source_seconds": round(duration, 2),
        "warning": warning,
        "beats": [
            {"layout": beat["layout"], "start": round(beat["out_start"], 2), "end": round(beat["out_end"], 2), "note": beat.get("note") or ""}
            for beat in beats
        ],
    }


def kept_spans(
    duration: float,
    silence: list[tuple[float, float]],
    claps: list[float],
    words: list[dict] | None,
) -> list[tuple[float, float]]:
    """Speech with long pauses, slate claps, and everything after the word 'cut' removed."""
    spans = _invert_silence(duration, silence)
    cuts = [(max(0.0, clap - 0.05), clap + 0.18) for clap in claps]
    cuts.extend(_retake_ranges(words or [], claps))
    return _subtract(spans, cuts)


def _invert_silence(duration: float, silence: list[tuple[float, float]]) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silence:
        if start > cursor + 0.08:
            spans.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor > 0.08:
        spans.append((cursor, duration))
    return spans or [(0.0, duration)]


def _retake_ranges(words: list[dict], claps: list[float]) -> list[tuple[float, float]]:
    ranges = []
    for word in words:
        token = re.sub(r"[^\w]", "", str(word.get("word") or "")).lower()
        if token != "cut":
            continue
        start = float(word.get("start") or 0)
        nxt = next((clap for clap in claps if clap > start + 0.05), None)
        end = nxt + 0.18 if nxt is not None and nxt - start < 15 else float(word.get("end") or start) + 0.4
        ranges.append((start, end))
    return ranges


def _subtract(spans: list[tuple[float, float]], cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    for cut_start, cut_end in cuts:
        nxt: list[tuple[float, float]] = []
        for start, end in spans:
            if cut_end <= start or cut_start >= end:
                nxt.append((start, end))
                continue
            if start < cut_start:
                nxt.append((start, cut_start))
            if cut_end < end:
                nxt.append((cut_end, end))
        spans = nxt
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if end - start < 0.18:
            continue
        if merged and start - merged[-1][1] < 0.08:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _place_beats(beats: list[dict], kept: float, timed: list[dict]) -> list[dict]:
    if not beats:
        return []
    if timed:
        counts = [max(1, int(beat.get("words") or 0)) for beat in beats]
        total = sum(counts)
        cursor = 0
        placed = []
        for index, beat in enumerate(beats):
            share = len(timed) if index == len(beats) - 1 else int(round(len(timed) * counts[index] / total))
            share = max(0, min(share, len(timed) - cursor))
            chunk = timed[cursor:cursor + share]
            cursor += share
            item = dict(beat)
            if chunk:
                item["out_start"] = chunk[0]["start"]
                item["out_end"] = chunk[-1]["end"]
            else:
                item["out_start"] = item.get("out_end", kept)
                item["out_end"] = item["out_start"]
            placed.append(item)
        return _fill_beat_gaps(placed, kept)
    total = sum(float(beat["duration"]) for beat in beats) or 1.0
    cursor = 0.0
    placed = []
    for beat in beats:
        length = kept * float(beat["duration"]) / total
        item = dict(beat)
        item["out_start"] = cursor
        item["out_end"] = cursor + length
        cursor += length
        placed.append(item)
    if placed:
        placed[-1]["out_end"] = kept
    return placed


def _fill_beat_gaps(beats: list[dict], kept: float) -> list[dict]:
    if not beats:
        return []
    beats[0]["out_start"] = 0.0
    for index, beat in enumerate(beats[:-1]):
        beat["out_end"] = max(beat["out_end"], beats[index + 1]["out_start"])
        if beat["out_end"] <= beat["out_start"]:
            beat["out_end"] = beat["out_start"] + 0.2
            beats[index + 1]["out_start"] = beat["out_end"]
    beats[-1]["out_end"] = max(beats[-1]["out_end"], kept)
    return [beat for beat in beats if beat["out_end"] - beat["out_start"] >= 0.12]


def _timed_words(words: list[dict], voice_spans: list[tuple[float, float]]) -> list[dict]:
    """Map source word times onto the compacted camera timeline."""
    timed = []
    for word in words:
        token = str(word.get("word") or "").strip()
        if re.sub(r"[^\w]", "", token).lower() == "cut":
            continue
        start = _source_to_output(voice_spans, float(word.get("start") or 0))
        end = _source_to_output(voice_spans, float(word.get("end") or 0))
        if start is None or end is None or end <= start:
            continue
        timed.append({"word": token, "start": start, "end": end})
    return timed


def _source_to_output(spans: list[tuple[float, float]], moment: float) -> float | None:
    cursor = 0.0
    for start, end in spans:
        if start <= moment <= end:
            return cursor + (moment - start)
        if moment > end:
            cursor += end - start
        else:
            return None
    return None


def _captions(beats: list[dict], timed: list[dict], highlight: str) -> list[dict]:
    if timed:
        return _group(timed, highlight)
    words = []
    for beat in beats:
        spoken = [part for part in str(beat.get("spoken") or "").split() if part]
        if not spoken:
            continue
        length = max(0.2, beat["out_end"] - beat["out_start"])
        step = length / len(spoken)
        for index, word in enumerate(spoken):
            words.append({
                "word": word,
                "start": beat["out_start"] + index * step,
                "end": beat["out_start"] + (index + 1) * step,
            })
    return _group(words, highlight)


def _group(words: list[dict], highlight: str) -> list[dict]:
    buckets: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        current.append(word)
        if len(current) >= 5:
            buckets.append(current)
            current = []
    if current:
        if buckets and len(buckets[-1]) + len(current) <= 7:
            buckets[-1].extend(current)
        else:
            buckets.append(current)
    token = highlight.strip().lower()
    groups = []
    for bucket in buckets:
        groups.append({
            "start": bucket[0]["start"],
            "end": max(bucket[-1]["end"], bucket[0]["start"] + 0.25),
            "words": [str(item["word"]) for item in bucket],
            "highlight": _highlight_word(bucket, token),
        })
    return groups


def _highlight_word(bucket: list[dict], token: str) -> str:
    cleaned = [re.sub(r"[^\w]", "", str(item["word"])) for item in bucket]
    if token:
        for word in cleaned:
            if word.lower() == token:
                return word
    ranked = [word for word in cleaned if word.lower() not in STOP and len(word) > 2]
    return max(ranked, key=len) if ranked else (cleaned[-1] if cleaned else "")


def _slices(beats: list[dict], spans: list[tuple[float, float]]) -> list[dict]:
    pieces = []
    for beat in beats:
        cursor = 0.0
        for start, end in spans:
            length = end - start
            out_start = cursor
            out_end = cursor + length
            cursor += length
            lo = max(beat["out_start"], out_start)
            hi = min(beat["out_end"], out_end)
            if hi - lo < 0.12:
                continue
            src = start + (lo - out_start)
            pieces.append({
                "layout": beat["layout"],
                "note": beat.get("note") or "",
                "src": src,
                "duration": hi - lo,
                "out": lo,
            })
    return pieces


def _render_segment(
    dest: Path,
    segment: dict,
    camera: Path,
    mic: Path | None,
    mic_offset: float,
    screen: Path | None,
    screen_offset: float | None,
    screen_cursor: float,
    label: Path,
) -> float:
    duration = segment["duration"]
    audio_path, audio_start = _audio_source(camera, mic, segment["src"], duration, mic_offset)
    layout = segment["layout"]
    note = segment["note"]
    if layout == "screen" and screen is not None:
        synced = screen_offset is not None
        if synced:
            screen_start = max(0.0, segment["src"] + screen_offset)
        else:
            screen_start = screen_cursor
            screen_cursor += duration
        zoom = 1.35 if "zoom" in note.lower() else 1.0
        video_filter = _cover(zoom) + "," + _label_filter(label)
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{screen_start:.3f}", "-t", f"{duration:.3f}", "-i", str(screen),
            "-ss", f"{audio_start:.3f}", "-t", f"{duration:.3f}", "-i", str(audio_path),
            "-filter_complex", f"[0:v]{video_filter}[v];[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a]",
            "-map", "[v]", "-map", "[a]",
            * _encode(), str(dest),
        ])
        return screen_cursor
    if layout == "card":
        still = dest.with_suffix(".png")
        _card_image(note or " ", still, None)
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-framerate", "30", "-t", f"{duration:.3f}", "-i", str(still),
            "-ss", f"{audio_start:.3f}", "-t", f"{duration:.3f}", "-i", str(audio_path),
            "-filter_complex", "[0:v]scale=1920:1080,setsar=1,fps=30[v];[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a]",
            "-map", "[v]", "-map", "[a]",
            *_encode(), str(dest),
        ])
        return screen_cursor
    video_filter = _cover(1.0)
    if layout == "broll":
        video_filter = "scale=480:270,boxblur=12:2,scale=1920:1080,setsar=1,fps=30," + _label_filter(label)
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{segment['src']:.3f}", "-t", f"{duration:.3f}", "-i", str(camera),
        "-ss", f"{audio_start:.3f}", "-t", f"{duration:.3f}", "-i", str(audio_path),
        "-filter_complex", f"[0:v]{video_filter}[v];[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a]",
        "-map", "[v]", "-map", "[a]",
        *_encode(), str(dest),
    ])
    return screen_cursor


def _audio_source(camera: Path, mic: Path | None, camera_start: float, duration: float, offset: float) -> tuple[Path, float]:
    if mic is not None:
        mic_start = camera_start + offset
        mic_duration = sync.probe_duration(mic) or 0
        if mic_start >= 0 and mic_start + duration <= mic_duration + 0.08:
            return mic, mic_start
    return camera, max(0.0, camera_start)


def _card_image(text: str, dest: Path, frame: Path | None) -> None:
    if frame and frame.is_file():
        image = Image.open(frame).convert("RGB").resize((WIDTH, HEIGHT))
        image = image.filter(ImageFilter.GaussianBlur(radius=18))
    else:
        image = Image.new("RGB", (WIDTH, HEIGHT), (8, 8, 8))
    shade = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    image = Image.blend(image, shade, 0.45)
    draw = ImageDraw.Draw(image)
    line = " ".join(text.upper().split())[:42] or " "
    size = 140
    font = ImageFont.truetype(str(FONT), size)
    while size > 48 and font.getlength(line) > WIDTH - 160:
        size -= 8
        font = ImageFont.truetype(str(FONT), size)
    width = font.getlength(line)
    x = (WIDTH - width) / 2
    y = HEIGHT / 2 - size
    draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0))
    draw.text((x, y), line, font=font, fill=(255, 255, 255))
    image.save(dest)


def _concat(paths: list[Path], dest: Path, work: Path) -> None:
    if not paths:
        raise CutError("nothing to concatenate")
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{path.resolve()}'\n" for path in paths))
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", str(dest),
    ])


def _finish(master: Path, ass: Path, music: Path | None, ticks: Path | None, dest: Path) -> None:
    fonts = _filter_path(FONT.parent)
    captions = _filter_path(ass)
    video = f"[0:v]subtitles={captions}:fontsdir={fonts}[v]"
    if music is None and ticks is None:
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(master),
            "-filter_complex", video,
            "-map", "[v]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            str(dest),
        ])
        return
    inputs = ["-i", str(master)]
    if music is not None:
        inputs += ["-i", str(music)]
    if ticks is not None:
        inputs += ["-i", str(ticks)]
    voice = "[0:a]aformat=sample_rates=48000:channel_layouts=stereo"
    parts = [video, voice + (",asplit=2[voice][side]" if music is not None else "[voice]")]
    mix = ["[voice]"]
    next_index = 1
    if music is not None:
        parts.append(f"[{next_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.9[music]")
        parts.append("[music][side]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=300[bed]")
        mix.append("[bed]")
        next_index += 1
    if ticks is not None:
        parts.append(f"[{next_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.4[fx]")
        mix.append("[fx]")
    parts.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0[a]")
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", ";".join(parts),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        str(dest),
    ])


def _music_bed(work: Path, intro: Path | None, body: Path | None, switch: float, total: float) -> Path | None:
    if intro is None and body is None:
        return None
    dest = work / "music.wav"
    if intro and body and 0.4 < switch < total - 0.3:
        head = work / "music-head.wav"
        tail = work / "music-tail.wav"
        _loop_audio(intro, head, switch, fade_out=True)
        _loop_audio(body, tail, total - switch, fade_in=True)
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(head), "-i", str(tail),
            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[a]",
            "-map", "[a]", str(dest),
        ])
        return dest
    _loop_audio(intro or body, dest, total, fade_in=False)
    return dest


def _loop_audio(source: Path, dest: Path, seconds: float, fade_out: bool = False, fade_in: bool = False) -> None:
    filters = [f"atrim=0:{seconds:.3f}", "asetpts=PTS-STARTPTS", "aformat=sample_rates=48000:channel_layouts=stereo"]
    if fade_in:
        filters.append("afade=t=in:st=0:d=0.25")
    if fade_out and seconds > 0.4:
        filters.append(f"afade=t=out:st={max(0.0, seconds - 0.25):.3f}:d=0.25")
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-stream_loop", "-1", "-i", str(source),
        "-t", f"{seconds:.3f}",
        "-af", ",".join(filters),
        str(dest),
    ])


def _ticks(dest: Path, duration: float, moments: list[float]) -> Path | None:
    if not moments:
        return None
    rate = 48000
    samples = np.zeros(int(duration * rate) + rate, dtype=np.float32)
    click = int(0.07 * rate)
    curve = np.sin(2 * np.pi * 180 * np.arange(click) / rate) * np.linspace(0.4, 0, click)
    for moment in moments:
        start = int(moment * rate)
        end = min(len(samples), start + click)
        if start < 0 or start >= len(samples):
            continue
        samples[start:end] += curve[: end - start]
    pcm = np.clip(samples, -1, 1)
    stereo = np.column_stack((pcm, pcm))
    ints = (stereo * 16000).astype(np.int16)
    with wave.open(str(dest), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(ints.tobytes())
    return dest


def _write_ass(path: Path, groups: list[dict], accent: str) -> None:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Caption,Inter,62,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,0,2,90,90,72,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for group in groups:
        text = _ass_line(group["words"], group["highlight"], accent)
        lines.append(f"Dialogue: 0,{_ass_time(group['start'])},{_ass_time(group['end'])},Caption,,0,0,0,,{text}")
    path.write_text("\n".join(lines))


def _ass_line(words: list[str], highlight: str, accent: str) -> str:
    parts = []
    matched = False
    for word in words:
        clean = _ass_escape(word)
        token = re.sub(r"[^\w]", "", word)
        if not matched and highlight and token.lower() == highlight.lower():
            parts.append(f"{{\\c&H{accent}&}}{clean}{{\\c&HFFFFFF&}}")
            matched = True
        else:
            parts.append(clean)
    return " ".join(parts)


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = seconds % 60
    return f"{hours}:{minutes:02d}:{rest:05.2f}"


def _silence_ranges(path: Path, duration: float) -> list[tuple[float, float]]:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(path),
            "-af", "silencedetect=noise=-35dB:d=0.45",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    events = []
    for match in re.finditer(r"silence_start: (\d+(?:\.\d+)?)", result.stderr):
        events.append((match.start(), "start", float(match.group(1))))
    for match in re.finditer(r"silence_end: (\d+(?:\.\d+)?)", result.stderr):
        events.append((match.start(), "end", float(match.group(1))))
    events.sort()
    ranges = []
    opened = None
    for _, kind, moment in events:
        if kind == "start":
            opened = moment
        elif opened is not None:
            ranges.append((opened, moment))
            opened = None
    if opened is not None:
        ranges.append((opened, duration))
    return ranges


def _clap_times(path: Path, duration: float) -> list[float]:
    rate = 8000
    samples = sync._pcm(path, duration + 0.1, rate)
    if samples.size < rate // 2:
        return []
    magnitude = np.abs(samples.astype(np.float32))
    median = float(np.median(magnitude)) + 1.0
    threshold = max(12.0 * median, 14000.0)
    times = []
    index = 0
    step = int(0.01 * rate)
    while index < magnitude.size:
        if magnitude[index] >= threshold:
            window = magnitude[index:index + int(0.06 * rate)]
            peak = index + int(np.argmax(window))
            times.append(peak / rate)
            index = peak + int(0.45 * rate)
        else:
            index += step
    return times


def _shift_spans(spans: list[tuple[float, float]], delta: float, duration: float) -> list[tuple[float, float]]:
    shifted = []
    for start, end in spans:
        lo = max(0.0, start + delta)
        hi = min(duration, end + delta)
        if hi - lo >= 0.18:
            shifted.append((lo, hi))
    return shifted


def _cover(zoom: float) -> str:
    width = int(WIDTH * zoom)
    height = int(HEIGHT * zoom)
    width -= width % 2
    height -= height % 2
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1,fps=30"


def _label_filter(textfile: Path) -> str:
    font = _filter_path(FONT)
    path = _filter_path(textfile)
    return (
        f"drawtext=fontfile={font}:textfile={path}:fontsize=34:fontcolor=white:"
        "box=1:boxcolor=black@0.78:boxborderw=14:x=96:y=h-250,"
        "drawbox=x=40:y=h-242:w=36:h=36:color=0xFF3B30@0.95:t=fill"
    )


def _write_label(path: Path, note: str) -> None:
    text = " ".join((note or "screen").split())[:48]
    path.write_text(text or "screen")


def _encode() -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest"]


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _offset(footage: dict, key: str) -> float | None:
    report = (footage.get("sync") or {}).get(key) or {}
    if not report.get("ok"):
        return None if key == "camera_screen" else 0.0
    return float(report.get("offset_seconds") or 0)


def _existing(folder: Path, name: str | None) -> Path | None:
    if not name:
        return None
    path = folder / name
    return path if path.is_file() else None


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[-800:]
        raise CutError(detail or "ffmpeg failed")
