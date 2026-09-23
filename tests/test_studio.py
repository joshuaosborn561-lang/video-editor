import math
import struct
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import plan, store, suggest, sync, thumbnail
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


def test_brief_requires_the_fields_the_hooks_are_built_from(client: TestClient):
    response = client.post("/api/projects", json={"icp": "", "pain": "", "topic": "", "current_offer": "", "proof": ""})
    assert response.status_code == 400


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
