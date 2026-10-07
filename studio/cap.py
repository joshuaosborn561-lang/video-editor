"""Download a public Cap share. Private and password links are refused."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

MAX_BYTES = 2 * 1024 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{4,80}$")
_TITLE = re.compile(r'property="og:title"\s+content="([^"]+)"')
_TITLE_ALT = re.compile(r'content="([^"]+)"\s+property="og:title"')


class CapError(Exception):
    pass


def parse_share_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise CapError("Paste a public https://cap.so/s/… link.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"cap.so", "www.cap.so"}:
        raise CapError("Paste a public https://cap.so/s/… link.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"s", "embed"} and _ID.match(parts[1]):
        return parts[1]
    raise CapError("That link is not a Cap share or embed URL.")


def download(url: str, dest: Path) -> dict:
    video_id = parse_share_url(url)
    playlist = f"https://cap.so/api/playlist?videoId={video_id}&videoType=mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(600.0, connect=20.0)) as client:
        title = _title(client, video_id)
        response = _open(client, playlist)
        try:
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if response.status_code in {401, 403}:
                raise CapError("This Cap is private or password protected. Set it to public, anyone with the link.")
            if response.status_code == 404:
                raise CapError("Cap not found. Check the share link.")
            if response.status_code >= 400:
                raise CapError(f"Cap refused the download ({response.status_code}).")
            if "mpegurl" in content_type or content_type in {"application/vnd.apple.mpegurl", "application/x-mpegurl"}:
                playlist_path = dest.with_suffix(".m3u8")
                _save(response, playlist_path)
                _remux(playlist_path, dest)
            elif content_type.startswith("video/") or content_type in {"application/octet-stream", ""}:
                _save(response, dest)
            else:
                raise CapError("Cap did not return a video file. Set the share to public and try again.")
        finally:
            response.close()
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise CapError("Cap returned an empty video.")
    return {"video_id": video_id, "title": title, "url": f"https://cap.so/s/{video_id}"}


def _title(client: httpx.Client, video_id: str) -> str:
    page = _open(client, f"https://cap.so/s/{video_id}")
    try:
        if page.status_code != 200:
            return ""
        text = page.read().decode("utf-8", "replace")
    finally:
        page.close()
    match = _TITLE.search(text) or _TITLE_ALT.search(text)
    if not match:
        return ""
    title = match.group(1).replace("&amp;", "&").strip()
    suffix = " | Cap Recording"
    if title.endswith(suffix):
        title = title[: -len(suffix)].strip()
    return title


def _open(client: httpx.Client, url: str) -> httpx.Response:
    current = url
    for _ in range(4):
        _check_host(current)
        response = client.send(client.build_request("GET", current), stream=True, follow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise CapError("Cap did not provide the video file.")
            current = urljoin(current, location)
            continue
        return response
    raise CapError("Cap sent too many redirects.")


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed = host == "cap.so" or host.endswith(".cap.so") or host.endswith(".cloudfront.net") or host.endswith(".amazonaws.com")
    if parsed.scheme != "https" or not allowed:
        raise CapError("Cap sent the file from a host the desk does not fetch.")


def _save(response: httpx.Response, dest: Path) -> None:
    written = 0
    with dest.open("wb") as handle:
        for chunk in response.iter_bytes(1024 * 1024):
            written += len(chunk)
            if written > MAX_BYTES:
                raise CapError("That Cap is over 2 GB. Export a smaller file and upload it.")
            handle.write(chunk)


def _remux(playlist: Path, dest: Path) -> None:
    completed = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(playlist), "-c", "copy", str(dest)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not dest.is_file():
        detail = _redact(completed.stderr)[-240:]
        raise CapError(f"Could not read the Cap stream. {detail}".strip())


def _redact(text: str) -> str:
    return re.sub(r"https://\S+", "https://…", text or "")
