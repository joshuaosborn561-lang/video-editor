import math
import struct
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import cloud, plan, store, suggest, sync, thumbnail
from studio.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    return TestClient(app)


def brief():
    return {
        "founder_name": "Alex",
        "company": "Northwind",
        "icp": "owners of B2B service companies",
        "pain": "referrals dried up",
        "topic": "sales calls from YouTube",
        "current_offer": "done-for-you YouTube",
        "proof": "15 sales calls in 30 days from a channel under 800 subscribers",
        "cta": "book a call",
    }


def test_template_options_use_the_brief():
    suggestions = suggest.template_suggestions(brief())
    assert len(suggestions["offers"]) == 3
    assert len(suggestions["hooks"]) == 5
    assert len(suggestions["titles"]) == 3
    blob = " ".join(item["text"] for item in suggestions["hooks"])
    assert "owners of B2B service companies" in blob
    assert "15 sales calls" in blob
    labels = {item["label"] for item in suggestions["hooks"]}
    assert "Who / what / why" in labels
    assert "8-second proof" in labels


def test_long_face_stretch_is_flagged():
    script = "[[face]]\n" + ("word " * 120) + "\n[[card: 15 CALLS]]\nDone."
    beats = plan.parse_beats(script)
    issues = plan.cadence_issues(beats)
    assert beats[0]["duration"] > 30
    assert any("Face stays up" in issue for issue in issues)


def test_thumbnail_is_a_1280_glance(tmp_path: Path):
    face = tmp_path / "face.png"
    Image.new("RGB", (800, 1000), (40, 90, 140)).save(face)
    dest = tmp_path / "thumb.jpg"
    thumbnail.render(face, ["STOP", "LOSING THEM"], "STOP", dest, desaturate=True, accent="yellow")
    image = Image.open(dest)
    assert image.size == (1280, 720)


def test_clap_offset_matches_the_spike(tmp_path: Path):
    camera = tmp_path / "camera.wav"
    mic = tmp_path / "mic.wav"
    _wav(camera, spike_at=1.0)
    _wav(mic, spike_at=1.4)
    result = sync.clap_offset(camera, mic)
    assert result["ok"]
    assert result["offset_seconds"] == pytest.approx(0.4, abs=0.02)


def test_desk_flow_picks_thumbnail_script_and_price(client: TestClient):
    created = client.post("/api/projects", json=brief())
    assert created.status_code == 200
    project_id = created.json()["id"]

    suggested = client.post(f"/api/projects/{project_id}/suggest")
    assert suggested.status_code == 200
    body = suggested.json()
    assert body["suggestion_source"] == "template"

    picks = client.post(
        f"/api/projects/{project_id}/picks",
        json={
            "offer_id": body["suggestions"]["offers"][0]["id"],
            "hook_id": body["suggestions"]["hooks"][0]["id"],
            "title_id": body["suggestions"]["titles"][2]["id"],
            "thumb_lines": ["STOP", "LOSING THEM"],
            "highlight": "STOP",
            "accent": "yellow",
            "desaturate": True,
        },
    )
    assert picks.status_code == 200

    face = Path(store.ROOT) / "face-src.png"
    Image.new("RGB", (640, 480), (20, 20, 20)).save(face)
    with face.open("rb") as handle:
        rendered = client.post(
            f"/api/projects/{project_id}/thumbnail",
            files={"face": ("face.png", handle, "image/png")},
        )
    assert rendered.status_code == 200
    image = client.get(f"/api/projects/{project_id}/thumbnail.jpg")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"

    script = client.post(f"/api/projects/{project_id}/script")
    assert "[[face]]" in script.json()["script"]
    assert client.post(f"/api/projects/{project_id}/script/approve").status_code == 200

    priced = client.post(f"/api/projects/{project_id}/plan")
    assert priced.status_code == 200
    quote = priced.json()["plan"]["quote"]
    assert quote["total"] == 2.0
    assert quote["estimated_minutes"] is True

    approved = client.post(f"/api/projects/{project_id}/approve")
    assert approved.json()["approved"] is True
    assert (store.ROOT / project_id / "edit-plan.json").is_file()


def test_hook_preview_burns_a_caption(tmp_path: Path):
    from studio.preview import render_hook_preview

    camera = tmp_path / "camera.mp4"
    subprocess_ok = __import__("subprocess").run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(camera),
        ],
        check=True,
    )
    assert subprocess_ok.returncode == 0
    dest = tmp_path / "preview.mp4"
    render_hook_preview(camera, "15 calls in 30 days.", dest)
    assert dest.stat().st_size > 1000


def test_desk_has_no_password(client: TestClient, monkeypatch):
    monkeypatch.setenv("DESK_TOKEN", "secret-token")
    opened = client.post("/api/projects", json=brief())
    assert opened.status_code == 200
    vendors = client.get("/api/vendors")
    assert vendors.status_code == 200
    assert "auth_required" not in vendors.json()
    assert vendors.json()["storage"] == "disk"


def test_cloud_save_skips_local_json(tmp_path: Path, monkeypatch):
    records: dict[str, dict] = {}
    uploads: list[tuple[str, str]] = []

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://youtube_desk@example/postgres")
    monkeypatch.setenv("STUDIO_SCRATCH", str(tmp_path))
    monkeypatch.setattr(cloud, "upsert_project", lambda project: records.__setitem__(project["id"], dict(project)))
    monkeypatch.setattr(cloud, "fetch_project", lambda project_id: records.get(project_id))
    monkeypatch.setattr(
        cloud,
        "list_summaries",
        lambda: [
            {
                "id": item["id"],
                "updated_at": item["updated_at"],
                "topic": item["brief"]["topic"],
                "approved": item["approved"],
            }
            for item in records.values()
        ],
    )
    monkeypatch.setattr(cloud, "upload", lambda project_id, path: uploads.append((project_id, path.name)))

    local = TestClient(app)
    created = local.post("/api/projects", json=brief())
    assert created.status_code == 200
    project_id = created.json()["id"]
    assert project_id in records
    assert not (store.ROOT / project_id / "project.json").exists()
    loaded = local.get(f"/api/projects/{project_id}")
    assert loaded.json()["brief"]["topic"] == "sales calls from YouTube"
    assert local.get("/api/projects").json()[0]["id"] == project_id

    face = tmp_path / "face-src.png"
    Image.new("RGB", (640, 480), (20, 20, 20)).save(face)
    suggested = local.post(f"/api/projects/{project_id}/suggest").json()
    local.post(
        f"/api/projects/{project_id}/picks",
        json={
            "offer_id": suggested["suggestions"]["offers"][0]["id"],
            "hook_id": suggested["suggestions"]["hooks"][0]["id"],
            "title_id": suggested["suggestions"]["titles"][0]["id"],
        },
    )
    with face.open("rb") as handle:
        rendered = local.post(
            f"/api/projects/{project_id}/thumbnail",
            files={"face": ("face.png", handle, "image/png")},
        )
    assert rendered.status_code == 200
    assert (project_id, "thumbnail.jpg") in uploads
    assert (project_id, "face.jpg") in uploads
    image = local.get(f"/api/projects/{project_id}/thumbnail.jpg")
    assert image.status_code == 200


def test_cut_drops_the_retake_and_the_pause():
    from studio.cut import kept_spans

    spans = kept_spans(
        10,
        [(4.0, 5.0)],
        [5.2],
        [{"word": "cut", "start": 3.4, "end": 3.7}],
    )
    assert spans[0][1] == pytest.approx(3.4, abs=0.05)
    assert spans[-1][0] > 5.2
    assert spans[-1][1] == pytest.approx(10, abs=0.05)


def test_effects_hit_cards_and_leave_face_cuts_dry():
    from studio.cut import effect_cues

    cues = effect_cues([
        {"layout": "face", "out_start": 0.0},
        {"layout": "card", "out_start": 2.0},
        {"layout": "screen", "out_start": 4.0},
        {"layout": "face", "out_start": 6.0},
        {"layout": "card", "out_start": 8.0},
    ])
    kinds = [(cue["kind"], cue["at"]) for cue in cues]
    assert ("riser", 2.0) in kinds
    assert ("hit", 2.0) in kinds
    assert ("click", 4.0) in kinds
    assert ("hit", 8.0) in kinds
    assert not any(cue["at"] in (0.0, 6.0) for cue in cues)


def test_full_cut_changes_picture_and_burns_captions(tmp_path: Path):
    from studio.cut import render_cut

    voice = tmp_path / "voice.wav"
    _tone_with_gap(voice, seconds=6.4, gap=(1.4, 4.4))
    camera = tmp_path / "camera.mp4"
    screen = tmp_path / "screen.mp4"
    intro = tmp_path / "intro.wav"
    body = tmp_path / "body.wav"
    _video_with_audio(camera, voice, "0x224466")
    _video_with_audio(screen, voice, "0x88AA44")
    _tone_with_gap(intro)
    _tone_with_gap(body)
    script = "\n".join([
        "[[face]]",
        "Hello there this is the hook for the video right now.",
        "[[card: 15 CALLS]]",
        "That number is the proof.",
        "[[screen: the inbox]]",
        "Here is the tool full frame.",
        "[[broll: the notebook]]",
        "Write this down now.",
    ])
    dest = tmp_path / "cut.mp4"
    report = render_cut(
        tmp_path,
        script,
        {"highlight": "CALLS", "accent": "yellow", "hook": {"text": "Hello there"}},
        {
            "camera": "camera.mp4",
            "screen": "screen.mp4",
            "music_intro": "intro.wav",
            "music_body": "body.wav",
            "sync": {},
        },
        dest,
    )
    assert dest.stat().st_size > 1000
    assert report["captions"] == "script"
    assert report["kept_seconds"] < report["source_seconds"] - 0.5
    layouts = [beat["layout"] for beat in report["beats"]]
    assert layouts[:4] == ["face", "card", "screen", "broll"]
    width, height = _frame_size(dest)
    assert (width, height) == (1920, 1080)
    face = _pixel(dest, tmp_path / "face.png", 0.15, 20, 20)
    assert face[2] > face[0]
    card = next(beat for beat in report["beats"] if beat["layout"] == "card")
    card_pixel = _pixel(dest, tmp_path / "card.png", card["start"] + 0.15, 20, 20)
    assert max(card_pixel[:3]) < 30
    caption = _pixel(dest, tmp_path / "caption.png", 0.2, 960, 980)
    assert max(caption[:3]) > 180


def test_brief_requires_the_fields_the_hooks_are_built_from(client: TestClient):
    response = client.post("/api/projects", json={"icp": "", "pain": "", "topic": "", "current_offer": "", "proof": ""})
    assert response.status_code == 400


def _tone_with_gap(path: Path, rate: int = 8000, seconds: float = 4.6, gap: tuple[float, float] = (1.6, 3.0)) -> None:
    frames = bytearray()
    for index in range(int(rate * seconds)):
        moment = index / rate
        if gap[0] <= moment < gap[1]:
            sample = 0
        else:
            sample = int(12000 * math.sin(2 * math.pi * 220 * moment))
        frames += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


def _video_with_audio(path: Path, audio: Path, color: str) -> None:
    subprocess = __import__("subprocess")
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=640x360:d=30",
            "-i", str(audio),
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


def _frame_size(path: Path) -> tuple[int, int]:
    subprocess = __import__("subprocess")
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    width, height = result.stdout.strip().split("x")
    return int(width), int(height)


def _pixel(video: Path, dest: Path, moment: float, x: int, y: int) -> tuple:
    subprocess = __import__("subprocess")
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{moment:.3f}", "-i", str(video), "-frames:v", "1", str(dest),
        ],
        check=True,
    )
    return Image.open(dest).convert("RGB").getpixel((x, y))


def _wav(path: Path, spike_at: float, rate: int = 8000, seconds: int = 5) -> None:
    frames = bytearray()
    for index in range(rate * seconds):
        moment = index / rate
        sample = int(800 * math.sin(2 * math.pi * 220 * moment))
        if abs(moment - spike_at) < 0.004:
            sample = 32000
        frames += struct.pack("<h", max(-32767, min(32767, sample)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
