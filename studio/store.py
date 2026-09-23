"""JSON project files on disk. One folder per video."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ID_RE = re.compile(r"^[a-f0-9]{12}$")
ROOT = Path(os.environ.get("STUDIO_DATA", "data/projects")).resolve()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_dir(project_id: str) -> Path:
    if not ID_RE.match(project_id):
        raise ValueError("unknown project")
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
    ROOT.mkdir(parents=True, exist_ok=True)
    project_id = uuid.uuid4().hex[:12]
    project = _blank(brief)
    project["id"] = project_id
    path = ROOT / project_id
    path.mkdir(parents=True)
    save(project)
    return project


def save(project: dict) -> dict:
    project["updated_at"] = now()
    path = ROOT / project["id"]
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.json").write_text(json.dumps(project, indent=2))
    return project


def load(project_id: str) -> dict:
    path = project_dir(project_id) / "project.json"
    return json.loads(path.read_text())


def list_projects() -> list[dict]:
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
