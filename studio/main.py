"""Approval desk for a B2B YouTube video. Suggest, pick, thumbnail, script, price."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from studio import cap, classify, cloud, cut, plan as plan_mod
from studio import preview, store, suggest, sync, thumbnail
from studio.llm import vendor_status

app = FastAPI(title="YouTube desk")
STATIC = Path(__file__).resolve().parent / "static"


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


class CapIn(BaseModel):
    url: str
    slot: str = "camera"


class CapFolderIn(BaseModel):
    url: str


class CapOrderIn(BaseModel):
    url: str = ""
    video_ids: list[str] = Field(default_factory=list)
    roles: dict[str, str] = Field(default_factory=dict)
    broll_ids: list[str] = Field(default_factory=list)


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
    return status


@app.get("/api/projects")
def projects() -> list[dict]:
    return store.list_projects()


@app.post("/api/projects")
def create_project(brief: BriefIn) -> dict:
    _require_brief(brief)
    project = store.create(brief.model_dump())
    project["kind"] = "pre"
    return store.save(project)


@app.post("/api/edits")
def create_edit() -> dict:
    project = store.create(
        {
            "founder_name": "",
            "company": "",
            "icp": "",
            "pain": "",
            "topic": "Edit",
            "current_offer": "",
            "proof": "",
            "cta": "",
        }
    )
    project["kind"] = "edit"
    return store.save(project)


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


@app.post("/api/images")
async def create_image(
    face: UploadFile | None = File(default=None),
    line1: str = Form(""),
    line2: str = Form(""),
    line3: str = Form(""),
    highlight: str = Form(""),
    accent: str = Form("yellow"),
    desaturate: str = Form("true"),
) -> dict:
    lines = [line.strip() for line in (line1, line2, line3) if line.strip()]
    project = store.create(
        {
            "founder_name": "",
            "company": "",
            "icp": "",
            "pain": "",
            "topic": " ".join(lines) or "Image",
            "current_offer": "",
            "proof": "",
            "cta": "",
        }
    )
    project["kind"] = "image"
    project["picks"] = {
        "thumb_lines": lines,
        "highlight": highlight.strip() or (lines[0] if lines else ""),
        "accent": "red" if accent == "red" else "yellow",
        "desaturate": desaturate.lower() in {"1", "true", "on", "yes"},
    }
    store.save(project)
    return await make_thumbnail(project["id"], face)


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


@app.get("/api/projects/{project_id}/cut.mp4")
def download_cut(project_id: str) -> FileResponse:
    return _file(project_id, "cut.mp4", "video/mp4", "cut not rendered")


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


@app.post("/api/cap-folder")
def preview_cap_folder(body: CapFolderIn) -> dict:
    try:
        return cap.list_collection(body.url)
    except cap.CapError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/projects/{project_id}/cap-folder")
def pull_cap_folder(project_id: str, body: CapOrderIn) -> dict:
    project = _project_or_404(project_id)
    listing = {"title": "", "url": body.url, "videos": []}
    if body.url.strip():
        try:
            listing = cap.list_collection(body.url)
        except cap.CapError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not body.video_ids and not body.broll_ids:
        raise HTTPException(status_code=400, detail="Choose at least one video.")
    if len(set(body.video_ids)) != len(body.video_ids):
        raise HTTPException(status_code=400, detail="Each video can be in the edit once.")
    folder = _ensure_footage(project_id, project.get("footage") or {})
    footage = project.setdefault("footage", {})
    known = {video["id"]: video for video in listing["videos"]}
    uploads = {item["id"]: item for item in footage.get("uploads") or []}
    try:
        caps, brolls = _take_order(folder, footage, known, uploads, body)
    except cap.CapError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sequence = bool(brolls) or any(item.get("source") == "file" for item in caps)
    if sequence:
        pieces = [piece for piece in classify.plan_sequence(caps, brolls) if piece["duration"] >= 0.2]
        if not pieces:
            raise HTTPException(status_code=400, detail="Nothing left to cut after the order.")
        footage["sequence"] = pieces
        footage["sequence_note"] = classify.describe(caps, brolls)
        footage["broll"] = [_saved_clip(item) for item in brolls]
        picture = next((item["file"] for item in brolls + caps if item.get("file")), "")
        footage["camera"] = picture
        footage["camera_seconds"] = round(sum(piece["duration"] for piece in pieces), 3)
    else:
        paths = [folder / item["file"] for item in caps]
        camera = folder / "camera.mp4"
        try:
            cap.stitch(paths, camera)
        except cap.CapError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.publish(project_id, camera)
        footage["camera"] = camera.name
        footage.pop("sequence", None)
        footage.pop("broll", None)
        footage["camera_seconds"] = sync.probe_duration(camera)
    footage["clips"] = [_saved_clip(item) for item in caps]
    footage["cap_url"] = listing.get("url") or ""
    footage["cap_title"] = listing.get("title") or ""
    if listing.get("title") and project.get("kind") == "edit":
        project.setdefault("brief", {})["topic"] = listing["title"]
    footage["sync"] = _sync_report(folder, footage)
    project["plan"] = None
    project["approved"] = False
    return store.save(project)


@app.post("/api/projects/{project_id}/cap")
def pull_cap(project_id: str, body: CapIn) -> dict:
    if body.slot not in {"camera", "screen"}:
        raise HTTPException(status_code=400, detail="choose the main recording or the screen recording")
    project = _project_or_404(project_id)
    folder = _ensure_footage(project_id, project.get("footage") or {})
    dest = folder / f"{body.slot}.mp4"
    try:
        info = cap.download(body.url, dest)
    except cap.CapError as exc:
        if dest.is_file():
            dest.unlink()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.publish(project_id, dest)
    footage = project.setdefault("footage", {})
    footage[body.slot] = dest.name
    footage["cap_url"] = info["url"]
    footage["cap_title"] = info["title"]
    if info["title"] and project.get("kind") == "edit":
        project.setdefault("brief", {})["topic"] = info["title"]
    footage["cap_slot"] = body.slot
    footage["sync"] = _sync_report(folder, footage)
    if footage.get("camera"):
        footage["camera_seconds"] = sync.probe_duration(folder / footage["camera"])
    project["plan"] = None
    project["approved"] = False
    return store.save(project)


@app.post("/api/projects/{project_id}/footage")
async def upload_footage(
    project_id: str,
    camera: UploadFile | None = File(default=None),
    clips: list[UploadFile] | None = File(default=None),
    mic: UploadFile | None = File(default=None),
    screen: UploadFile | None = File(default=None),
    music_intro: UploadFile | None = File(default=None),
    music_body: UploadFile | None = File(default=None),
    sfx_hit: UploadFile | None = File(default=None),
    sfx_click: UploadFile | None = File(default=None),
    sfx_riser: UploadFile | None = File(default=None),
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
        "sfx_hit": sfx_hit,
        "sfx_click": sfx_click,
        "sfx_riser": sfx_riser,
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
    if clips:
        footage["uploads"] = _store_uploads(project_id, folder, footage, clips)
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
    footage = project.get("footage") or {}
    seconds = footage.get("camera_seconds")
    minutes = (seconds / 60.0) if seconds else None
    if project.get("script_approved"):
        project["plan"] = plan_mod.build_plan(
            project["script"],
            project.get("picks") or {},
            footage,
            minutes,
            vendor_status(),
        )
    elif footage.get("camera") or footage.get("sequence"):
        project["plan"] = plan_mod.build_edit(footage, minutes, vendor_status())
    else:
        raise HTTPException(status_code=400, detail="attach the video first")
    project["approved"] = False
    return store.save(project)


@app.post("/api/projects/{project_id}/approve")
def approve_plan(project_id: str) -> dict:
    project = _project_or_404(project_id)
    if not project.get("plan"):
        raise HTTPException(status_code=400, detail="build the plan and the price first")
    camera_name = (project.get("footage") or {}).get("camera")
    if camera_name:
        folder = _ensure_footage(project_id, project.get("footage") or {})
        camera = folder / camera_name
        caption = ((project.get("picks") or {}).get("hook") or {}).get("text") or ""
        preview_path = folder / "preview.mp4"
        preview.render_hook_preview(camera, caption, preview_path)
        project["preview"] = "preview.mp4"
        store.publish(project_id, preview_path)
        cut_path = folder / "cut.mp4"
        try:
            project["render"] = cut.render_cut(
                folder,
                project.get("script") or "",
                project.get("picks") or {},
                project.get("footage") or {},
                cut_path,
            )
        except cut.CutError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        project["cut"] = "cut.mp4"
        store.publish(project_id, cut_path)
    project["approved"] = True
    saved = store.save(project)
    path = store.project_dir(project_id) / "edit-plan.json"
    path.write_text(json.dumps(saved["plan"], indent=2))
    store.publish(project_id, path)
    return saved


def _store_uploads(project_id: str, folder: Path, footage: dict, clips: list[UploadFile]) -> list[dict]:
    saved = []
    allowed = {".mp4", ".mov", ".mkv", ".webm"}
    for index, upload in enumerate(clips):
        if upload is None or not upload.filename:
            continue
        suffix = Path(upload.filename).suffix.lower() or ".mp4"
        if suffix not in allowed:
            raise HTTPException(status_code=400, detail="b-roll must be a video file")
        dest = folder / f"up{index}{suffix}"
        _save_upload(upload, dest)
        store.publish(project_id, dest)
        title = Path(upload.filename).stem
        item = {"id": f"up{index}", "file": dest.name, "title": title, "source": "file"}
        _measure(folder, footage.get("mic"), item)
        item["suggested_role"] = classify.suggest_role(title, item.get("sync"))
        saved.append(item)
    if not saved:
        raise HTTPException(status_code=400, detail="choose at least one video")
    return saved


def _take_order(folder: Path, footage: dict, known: dict, uploads: dict, body: CapOrderIn) -> tuple[list[dict], list[dict]]:
    caps = []
    seen = set()
    for video_id in body.video_ids:
        if video_id in seen:
            raise HTTPException(status_code=400, detail="Each video can be in the edit once.")
        seen.add(video_id)
        item = _resolve_clip(folder, known, uploads, video_id, body.roles.get(video_id))
        if item["role"] == "broll":
            continue
        _measure(folder, footage.get("mic"), item)
        if item["role"] in {"intro", "outro"}:
            item.pop("mic_start", None)
        caps.append(item)
    brolls = []
    for video_id in body.broll_ids:
        if video_id in seen:
            continue
        seen.add(video_id)
        item = _resolve_clip(folder, known, uploads, video_id, "broll")
        item["role"] = "broll"
        _measure(folder, footage.get("mic"), item)
        brolls.append(item)
    return caps, brolls


def _resolve_clip(folder: Path, known: dict, uploads: dict, video_id: str, role: str | None) -> dict:
    chosen = role if role in {"intro", "outro", "screen", "broll"} else ""
    if video_id in known:
        video = known[video_id]
        dest = folder / f"clip-{video_id}.mp4"
        cap.download(video["url"], dest)
        store.publish(folder.name, dest)
        return {
            "id": video_id,
            "title": video["title"],
            "file": dest.name,
            "source": "cap",
            "role": chosen or classify.role_from_title(video["title"]),
        }
    if video_id in uploads:
        upload = uploads[video_id]
        return {
            "id": video_id,
            "title": upload.get("title") or video_id,
            "file": upload["file"],
            "source": "file",
            "role": chosen or upload.get("suggested_role") or "screen",
        }
    raise HTTPException(status_code=400, detail="Those videos are not in this Cap folder.")


def _measure(folder: Path, mic_name: str | None, item: dict) -> None:
    path = folder / item["file"]
    item["duration"] = sync.probe_duration(path) or 0
    item.pop("mic_start", None)
    item.pop("clip_clap_at", None)
    if not mic_name:
        item["sync"] = {"ok": False, "reason": "Add the DJI mic so b-roll can lock to it."}
        return
    match = sync.match_to_mic(folder / mic_name, path)
    item["sync"] = match
    if match.get("ok"):
        item["mic_start"] = match.get("mic_start")
        item["clip_clap_at"] = match.get("clip_clap_at")


def _saved_clip(item: dict) -> dict:
    saved = {
        "id": item["id"],
        "title": item.get("title") or "",
        "file": item["file"],
        "role": item.get("role") or "screen",
    }
    if item.get("mic_start") is not None:
        saved["mic_start"] = item["mic_start"]
    if item.get("clip_clap_at") is not None:
        saved["clip_clap_at"] = item["clip_clap_at"]
    if item.get("duration"):
        saved["duration"] = item["duration"]
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
    names = []
    for name in ("camera", "mic", "screen", "music_intro", "music_body", "sfx_hit", "sfx_click", "sfx_riser"):
        if footage.get(name):
            names.append(footage[name])
    for key in ("uploads", "clips", "broll"):
        for item in footage.get(key) or []:
            if item.get("file"):
                names.append(item["file"])
    for piece in footage.get("sequence") or []:
        if piece.get("file"):
            names.append(piece["file"])
    for filename in names:
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


@app.get("/")
def editor_page() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/pre")
def pre_page() -> FileResponse:
    return FileResponse(STATIC / "pre.html")


@app.get("/image")
def image_page() -> FileResponse:
    return FileResponse(STATIC / "image.html")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
