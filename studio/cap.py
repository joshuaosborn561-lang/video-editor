"""Download a public Cap share or folder. Private and password links are refused."""

from __future__ import annotations

import html
import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from studio.sync import has_audio

MAX_BYTES = 2 * 1024 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{4,80}$")
_TITLE = re.compile(r'property="og:title"\s+content="([^"]+)"')
_TITLE_ALT = re.compile(r'content="([^"]+)"\s+property="og:title"')
_CARD = re.compile(
    r'href="/s/([A-Za-z0-9_-]{4,80})".{0,1600}?<h3[^>]*>\s*([^<]+?)\s*</h3>',
    re.S,
)
_FOLDER_LINK = re.compile(r'href="/c/([A-Za-z0-9_-]{4,80})"')
_MAX_PAGES = 8
_MAX_VIDEOS = 40


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


def parse_collection_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise CapError("Paste a public https://cap.so/c/… folder link.")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"cap.so", "www.cap.so"}:
        raise CapError("Paste a public https://cap.so/c/… folder link.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "c" and _ID.match(parts[1]):
        return parts[1]
    raise CapError("That link is not a Cap folder.")


def videos_from_html(page: str) -> list[dict]:
    found = []
    seen = set()
    for video_id, title in _CARD.findall(page):
        if video_id in seen:
            continue
        seen.add(video_id)
        found.append(
            {
                "id": video_id,
                "title": html.unescape(title).strip(),
                "url": f"https://cap.so/s/{video_id}",
            }
        )
    return found


def list_collection(url: str) -> dict:
    folder_id = parse_collection_url(url)
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        title, videos, truncated = _collect(client, folder_id)
    if not videos:
        raise CapError("That Cap folder has no public videos. Publish the folder, anyone with the link.")
    return {
        "id": folder_id,
        "title": title,
        "url": f"https://cap.so/c/{folder_id}",
        "videos": videos[:_MAX_VIDEOS],
        "truncated": truncated,
    }


def stitch(paths: list[Path], dest: Path) -> None:
    if len(paths) < 1:
        raise CapError("Choose at least one video.")
    if len(paths) == 1:
        dest.write_bytes(paths[0].read_bytes())
        return
    work = dest.parent / "_stitch"
    if work.exists():
        import shutil
        shutil.rmtree(work)
    work.mkdir(parents=True)
    normalized = []
    for index, path in enumerate(paths):
        out = work / f"{index:02d}.mp4"
        _normalize(path, out)
        normalized.append(out)
    listing = work / "list.txt"
    listing.write_text("".join(f"file '{path.name}'\n" for path in normalized), encoding="utf-8")
    completed = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not dest.is_file():
        raise CapError(f"Could not join the Cap videos. {_redact(completed.stderr)[-240:]}".strip())


def _collect(client: httpx.Client, folder_id: str) -> tuple[str, list[dict], bool]:
    videos: list[dict] = []
    seen: set[str] = set()
    first = _page(client, folder_id, 1)
    _guard_access(first)
    title = _collection_title(first)
    if _extend(videos, seen, videos_from_html(first)) < 0:
        return title, videos, True
    for page_number in range(2, _MAX_PAGES + 1):
        later = _page(client, folder_id, page_number)
        added = _extend(videos, seen, videos_from_html(later))
        if added < 0:
            return title, videos, True
        if added == 0:
            break
    for child_id in _folder_ids(first, folder_id)[:8]:
        child = _page(client, child_id, 1)
        if _blocked(child):
            continue
        if _extend(videos, seen, videos_from_html(child)) < 0:
            return title, videos, True
    return title, videos, False


def _page(client: httpx.Client, folder_id: str, page_number: int) -> str:
    url = f"https://cap.so/c/{folder_id}" if page_number <= 1 else f"https://cap.so/c/{folder_id}?page={page_number}"
    status, text = _read_text(client, url)
    if status == 404:
        raise CapError("Cap folder not found. Check the link.")
    if status in {401, 403}:
        raise CapError("This Cap folder is private. Publish it so anyone with the link can open it.")
    if status >= 400:
        raise CapError(f"Cap refused the folder ({status}).")
    return text


def _guard_access(page: str) -> None:
    if "Password required" in page or "Enter the collection password" in page:
        raise CapError("This Cap folder has a password. Turn the password off so anyone with the link can open it.")
    if "requires sign-in" in page or "Access restricted" in page:
        raise CapError("This Cap folder is private. Publish it so anyone with the link can open it.")


def _blocked(page: str) -> bool:
    return "Password required" in page or "requires sign-in" in page or "Access restricted" in page


def _collection_title(page: str) -> str:
    match = _TITLE.search(page) or _TITLE_ALT.search(page)
    if not match:
        return ""
    title = html.unescape(match.group(1)).strip()
    suffix = " | Cap Collection"
    if title.endswith(suffix):
        title = title[: -len(suffix)].strip()
    return title


def _folder_ids(page: str, current: str) -> list[str]:
    found = []
    for folder_id in _FOLDER_LINK.findall(page):
        if folder_id != current and folder_id not in found:
            found.append(folder_id)
    return found


def _extend(videos: list[dict], seen: set[str], found: list[dict]) -> int:
    added = 0
    for video in found:
        if video["id"] in seen:
            continue
        if len(videos) >= _MAX_VIDEOS:
            return -1
        seen.add(video["id"])
        videos.append(video)
        added += 1
    return added


def _read_text(client: httpx.Client, url: str) -> tuple[int, str]:
    response = _open(client, url)
    try:
        return response.status_code, response.read().decode("utf-8", "replace")
    finally:
        response.close()


def _normalize(src: Path, dest: Path) -> None:
    audio = has_audio(src)
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if audio:
        command += ["-map", "0:v:0", "-map", "0:a:0"]
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    command += [
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
        str(dest),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0 or not dest.is_file():
        raise CapError(f"Could not prepare a Cap video. {_redact(completed.stderr)[-240:]}".strip())


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
