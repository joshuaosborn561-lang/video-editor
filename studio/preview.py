"""Eight-second captioned preview from the camera file. The full cut stays behind approval."""

from __future__ import annotations

import subprocess
from pathlib import Path

FONT = Path(__file__).resolve().parent / "fonts" / "Inter-Bold.ttf"


def render_hook_preview(camera: Path, caption: str, dest: Path) -> Path:
    text_path = dest.with_suffix(".txt")
    line = " ".join(caption.split())[:110] or " "
    text_path.write_text(line)
    video_filter = (
        "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        f"drawtext=fontfile={FONT}:textfile={text_path}:fontsize=42:fontcolor=white:"
        "borderw=4:bordercolor=black:x=(w-text_w)/2:y=h-110"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(camera), "-t", "8",
            "-vf", video_filter,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest
