"""Approval desk for a B2B YouTube video. Suggest, pick, thumbnail, script, price."""

from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from studio import cloud, plan as plan_mod
from studio import preview, store, suggest, sync, thumbnail
from studio.llm import vendor_status

app = FastAPI(title="YouTube desk")
STATIC = Path(__file__).resolve().parent / "static"


class DeskTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("DESK_TOKEN", "")
        if expected and request.url.path.startswith("/api/") and request.url.path != "/api/vendors":
            supplied = _presented_token(request)
            if not supplied or not secrets.compare_digest(supplied, expected):
                return JSONResponse({"detail": "desk token required"}, status_code=401)
        return await call_next(request)


def _presented_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("desk_token") or request.headers.get("x-desk-token") or ""


app.add_middleware(DeskTokenMiddleware)

class BriefIn(BaseModel):
    founder_name: str = ""
    company: str = ""
    icp: str
    pain: str
    topic: str
    current_offer: str
    proof: str
    cta: str = "book a call"


class PicksIn(BaseModel):
    offer_id: str
    hook_id: str
    title_id: str
    thumb_lines: list[str] = Field(default_factory=list)
    highlight: str = ""
    accent: str = "yellow"
    desaturate: bool = True


class ScriptIn(BaseModel):
    text: str


def _project_or_404(project_id: str) -> dict:
    try:
        return store.load(project_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc


def _require_suggestions(project: dict) -> dict:
    if not project.get("suggestions"):
        raise HTTPException(status_code=400, detail="generate options before picking")
    return project["suggestions"]


@app.get("/api/vendors")
def vendors() -> dict:
    status = vendor_status()
    status["storage"] = "supabase" if cloud.enabled() else "disk"
    status["auth_required"] = bool(os.environ.get("DESK_TOKEN"))
    return status


@app.get("/api/projects")
def projects() -> list[dict]:
    return store.list_projects()


@app.post("/api/projects")
def create_project(brief: BriefIn) -> dict:
    _require_brief(brief)
    return store.create(brief.model_dump())


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    return _project_or_404(project_id)


@app.post("/api/projects/{project_id}/suggest")
def run_suggest(project_id: str) -> dict:
    project = _project_or_404(project_id)
    suggestions, source, warning = suggest.suggest(project["brief"])
    project["suggestions"] = suggestions
    project["suggestion_source"] = source
    project["suggestion_warning"] = warning
    project["picks"] = None
    project["script"] = None
    project["script_approved"] = False
    project["plan"] = None
    project["approved"] = False
    return store.save(project)


@app.post("/api/projects/{project_id}/picks")
def save_picks(project_id: str, picks: PicksIn) -> dict:
    project = _project_or_404(project_id)
    suggestions = _require_suggestions(project)
    offer = _find(suggestions["offers"], picks.offer_id, "offer")
    hook = _find(suggestions["hooks"], picks.hook_id, "hook")
    title = _find(suggestions["titles"], picks.title_id, "title")
    lines = [line.strip() for line in picks.thumb_lines if line.strip()] or list(title["thumb_lines"])
    if not 1 <= len(lines) <= 3:
        raise HTTPException(status_code=400, detail="use 1 to 3 thumbnail lines")
    highlight = (picks.highlight or title.get("highlight") or lines[0]).strip()
    project["picks"] = {
        "offer_id": offer["id"],
        "hook_id": hook["id"],
        "title_id": title["id"],
        "offer": offer,
        "hook": hook,
        "title": title,
        "thumb_lines": lines,
        "highlight": highlight,
        "accent": "red" if picks.accent == "red" else "yellow",
        "desaturate": picks.desaturate,
    }
    project["script_approved"] = False
    project["plan"] = None
    project["approved"] = False
    return store.save(project)


@app.post("/api/projects/{project_id}/thumbnail")
async def make_thumbnail(project_id: str, face: UploadFile | None = File(default=None)) -> dict:
    project = _project_or_404(project_id)
    if not project.get("picks"):
        raise HTTPException(status_code=400, detail="pick an offer, hook, and title first")
    folder = store.project_dir(project_id)
    source = folder / "face.jpg"
    if face is not None and face.filename:
        _save_upload(face, source)
        store.publish(project_id, source)
    elif not source.exists():
        try:
            source = store.materialize(project_id, "face.jpg")
        except FileNotFoundError:
            source = None
    dest = folder / "thumbnail.jpg"
    picks = project["picks"]
    try:
        thumbnail.render(
            source,
            picks["thumb_lines"],
            picks["highlight"],
            dest,
            desaturate=picks.get("desaturate", True),
            accent=picks.get("accent") or "yellow",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project["thumbnail"] = "thumbnail.jpg"
    saved = store.save(project)
    store.publish(project_id, dest)
    return saved


@app.get("/api/projects/{project_id}/preview.mp4")
def download_preview(project_id: str) -> FileResponse:
    return _file(project_id, "preview.mp4", "video/mp4", "preview not rendered")


@app.get("/api/projects/{project_id}/thumbnail.jpg")
def download_thumbnail(project_id: str) -> FileResponse:
    return _file(project_id, "thumbnail.jpg", "image/jpeg", "thumbnail not rendered")


@app.post("/api/projects/{project_id}/script")
def write_script(project_id: str) -> dict:
    project = _project_or_404(project_id)
    if not project.get("picks"):
        raise HTTPException(status_code=400, detail="pick an offer, hook, and title first")
    text, source, warning = suggest.script_from_picks(
        project["brief"], project["picks"], project["suggestions"]
    )
    project["script"] = text
    project["script_source"] = source
    project["suggestion_warning"] = warning
    project["script_approved"] = False
    project["plan"] = None
    project["approved"] = False
    return store.save(project)


@app.put("/api/projects/{project_id}/script")
def update_script(project_id: str, body: ScriptIn) -> dict:
    project = _project_or_404(project_id)
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="script is empty")
    project["script"] = body.text
    project["script_approved"] = False
    project["plan"] = None
    project["approved"] = False
    return store.save(project)


@app.post("/api/projects/{project_id}/script/approve")
def approve_script(project_id: str) -> dict:
    project = _project_or_404(project_id)
    if not (project.get("script") or "").strip():
        raise HTTPException(status_code=400, detail="write the script first")
    project["script_approved"] = True
    return store.save(project)


@app.post("/api/projects/{project_id}/footage")
async def upload_footage(
    project_id: str,
    camera: UploadFile | None = File(default=None),
    mic: UploadFile | None = File(default=None),
    screen: UploadFile | None = File(default=None),
    music_intro: UploadFile | None = File(default=None),
    music_body: UploadFile | None = File(default=None),
) -> dict:
    project = _project_or_404(project_id)
    folder = _ensure_footage(project_id, project.get("footage") or {})
    footage = project.setdefault("footage", {})
    mapping = {
        "camera": camera,
        "mic": mic,
        "screen": screen,
        "music_intro": music_intro,
        "music_body": music_body,
    }
    for name, upload in mapping.items():
        if upload is None or not upload.filename:
            continue
        suffix = Path(upload.filename).suffix.lower() or ".bin"
        if suffix not in {".mp4", ".mov", ".wav", ".mp3", ".m4a", ".aac", ".mkv", ".webm"}:
            raise HTTPException(status_code=400, detail=f"{name} must be audio or video")
        dest = folder / f"{name}{suffix}"
        _save_upload(upload, dest)
        store.publish(project_id, dest)
        footage[name] = dest.name
    footage["sync"] = _sync_report(folder, footage)
    camera_name = footage.get("camera")
    if camera_name:
        footage["camera_seconds"] = sync.probe_duration(folder / camera_name)
    project["plan"] = None
    project["approved"] = False
    return store.save(project)


@app.post("/api/projects/{project_id}/plan")
def build_plan(project_id: str) -> dict:
    project = _project_or_404(project_id)
    if not project.get("script_approved"):
        raise HTTPException(status_code=400, detail="approve the script before pricing the edit")
    footage = project.get("footage") or {}
    seconds = footage.get("camera_seconds")
    minutes = (seconds / 60.0) if seconds else None
    project["plan"] = plan_mod.build_plan(
        project["script"],
        project.get("picks") or {},
        footage,
        minutes,
        vendor_status(),
    )
    project["approved"] = False
    return store.save(project)


@app.post("/api/projects/{project_id}/approve")
def approve_plan(project_id: str) -> dict:
    project = _project_or_404(project_id)
    if not project.get("plan"):
        raise HTTPException(status_code=400, detail="build the plan and the price first")
    project["approved"] = True
    camera_name = (project.get("footage") or {}).get("camera")
    if camera_name:
        camera = store.materialize(project_id, camera_name)
        caption = ((project.get("picks") or {}).get("hook") or {}).get("text") or ""
        preview_path = store.project_dir(project_id) / "preview.mp4"
        preview.render_hook_preview(camera, caption, preview_path)
        project["preview"] = "preview.mp4"
        store.publish(project_id, preview_path)
    saved = store.save(project)
    path = store.project_dir(project_id) / "edit-plan.json"
    path.write_text(json.dumps(saved["plan"], indent=2))
    store.publish(project_id, path)
    return saved


def _sync_report(folder: Path, footage: dict) -> dict:
    report: dict = {}
    camera = footage.get("camera")
    mic = footage.get("mic")
    screen = footage.get("screen")
    if camera and mic:
        report["camera_mic"] = sync.clap_offset(folder / camera, folder / mic)
    if camera and screen:
        screen_path = folder / screen
        if sync.has_audio(screen_path):
            report["camera_screen"] = sync.clap_offset(folder / camera, screen_path)
        else:
            report["camera_screen"] = {
                "ok": False,
                "reason": "The screen recording has no audio. It will be placed as an insert where the script says [[screen: ]].",
            }
    return report


def _file(project_id: str, filename: str, media_type: str, missing: str) -> FileResponse:
    try:
        path = store.materialize(project_id, filename)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=missing) from exc
    return FileResponse(path, media_type=media_type)


def _ensure_footage(project_id: str, footage: dict) -> Path:
    folder = store.project_dir(project_id)
    for name in ("camera", "mic", "screen", "music_intro", "music_body"):
        filename = footage.get(name)
        if not filename:
            continue
        try:
            store.materialize(project_id, filename)
        except FileNotFoundError:
            continue
    return folder


def _save_upload(upload: UploadFile, dest: Path) -> None:
    with dest.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)


def _find(items: list[dict], item_id: str, label: str) -> dict:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise HTTPException(status_code=400, detail=f"unknown {label}")


def _require_brief(brief: BriefIn) -> None:
    missing = [field for field in ("icp", "pain", "topic", "current_offer", "proof") if not getattr(brief, field).strip()]
    if missing:
        raise HTTPException(status_code=400, detail="fill in " + ", ".join(missing))


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
