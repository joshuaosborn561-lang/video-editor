"""Project records. Local JSON, or Supabase when the server keys are set."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from studio import cloud

ID_RE = re.compile(r"^[a-f0-9]{12}$")
ROOT = Path(os.environ.get("STUDIO_DATA", "data/projects")).resolve()
FILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_dir(project_id: str) -> Path:
    if not ID_RE.match(project_id):
        raise ValueError("unknown project")
    if cloud.enabled():
        if fetch_record(project_id) is None:
            raise FileNotFoundError(project_id)
        path = _scratch_root() / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = ROOT / project_id
    if not path.is_dir():
        raise FileNotFoundError(project_id)
    return path


def _blank(brief: dict) -> dict:
    return {
        "id": "",
        "created_at": now(),
        "updated_at": now(),
        "brief": brief,
        "suggestions": None,
        "suggestion_source": None,
        "suggestion_warning": None,
        "picks": None,
        "script": None,
        "script_source": None,
        "script_approved": False,
        "thumbnail": None,
        "footage": {},
        "plan": None,
        "approved": False,
    }


def create(brief: dict) -> dict:
    project_id = uuid.uuid4().hex[:12]
    project = _blank(brief)
    project["id"] = project_id
    if not cloud.enabled():
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / project_id).mkdir(parents=True)
    save(project)
    return project


def save(project: dict) -> dict:
    project["updated_at"] = now()
    if cloud.enabled():
        cloud.upsert_project(project)
        return project
    path = ROOT / project["id"]
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.json").write_text(json.dumps(project, indent=2))
    return project


def load(project_id: str) -> dict:
    if cloud.enabled():
        project = fetch_record(project_id)
        if project is None:
            raise FileNotFoundError(project_id)
        return project
    path = project_dir(project_id) / "project.json"
    return json.loads(path.read_text())


def fetch_record(project_id: str) -> dict | None:
    if not ID_RE.match(project_id):
        raise ValueError("unknown project")
    return cloud.fetch_project(project_id)


def list_projects() -> list[dict]:
    if cloud.enabled():
        return cloud.list_summaries()
    if not ROOT.exists():
        return []
    found = []
    for folder in ROOT.iterdir():
        meta = folder / "project.json"
        if meta.is_file():
            project = json.loads(meta.read_text())
            brief = project.get("brief") or {}
            found.append(
                {
                    "id": project["id"],
                    "updated_at": project.get("updated_at"),
                    "topic": brief.get("topic") or "Untitled",
                    "approved": bool(project.get("approved")),
                }
            )
    found.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return found


def publish(project_id: str, path: Path) -> None:
    if cloud.enabled() and path.is_file():
        cloud.upload(project_id, path)


def materialize(project_id: str, filename: str) -> Path:
    if not FILE_RE.match(filename):
        raise ValueError("unknown file")
    folder = project_dir(project_id)
    path = folder / filename
    if path.is_file() and path.stat().st_size > 0:
        return path
    if cloud.enabled():
        data = cloud.download(project_id, filename)
        if data:
            path.write_bytes(data)
            return path
    raise FileNotFoundError(filename)


def _scratch_root() -> Path:
    return Path(os.environ.get("STUDIO_SCRATCH", "/tmp/desk"))
