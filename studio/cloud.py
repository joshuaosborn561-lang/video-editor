"""Supabase Postgres and Storage. Used only when both server env vars are set."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

BUCKET = "desk"
TABLE = "desk_projects"

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".json": "application/json",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
}


def enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))


def upsert_project(project: dict) -> None:
    response = _client().post(
        f"{_base()}/rest/v1/{TABLE}",
        headers={**_auth(), "content-type": "application/json", "prefer": "resolution=merge-duplicates"},
        json={"id": project["id"], "payload": project, "updated_at": project["updated_at"]},
    )
    _raise(response, "save project")


def fetch_project(project_id: str) -> dict | None:
    response = _client().get(
        f"{_base()}/rest/v1/{TABLE}",
        headers=_auth(),
        params={"id": f"eq.{project_id}", "select": "payload"},
    )
    _raise(response, "load project")
    rows = response.json()
    if not rows:
        return None
    return rows[0]["payload"]


def list_summaries() -> list[dict]:
    response = _client().get(
        f"{_base()}/rest/v1/{TABLE}",
        headers=_auth(),
        params={"select": "id,updated_at,payload", "order": "updated_at.desc"},
    )
    _raise(response, "list projects")
    found = []
    for row in response.json():
        payload = row.get("payload") or {}
        brief = payload.get("brief") or {}
        found.append(
            {
                "id": row["id"],
                "updated_at": row.get("updated_at") or payload.get("updated_at"),
                "topic": brief.get("topic") or "Untitled",
                "approved": bool(payload.get("approved")),
            }
        )
    return found


def upload(project_id: str, path: Path) -> None:
    content_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    with path.open("rb") as handle:
        response = _client().post(
            f"{_base()}/storage/v1/object/{BUCKET}/{project_id}/{path.name}",
            headers={**_auth(), "content-type": content_type, "x-upsert": "true"},
            content=handle,
        )
    _raise(response, f"upload {path.name}")


def download(project_id: str, filename: str) -> bytes | None:
    response = _client().get(
        f"{_base()}/storage/v1/object/{BUCKET}/{project_id}/{filename}",
        headers=_auth(),
    )
    if response.status_code == 404:
        return None
    _raise(response, f"download {filename}")
    return response.content


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/")


def _auth() -> dict[str, str]:
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return {"apikey": key, "authorization": f"Bearer {key}"}


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(600.0, connect=20.0))


def _raise(response: httpx.Response, action: str) -> None:
    if response.status_code >= 400:
        detail = response.text[:300]
        raise RuntimeError(f"supabase {action} failed ({response.status_code}): {detail}")
