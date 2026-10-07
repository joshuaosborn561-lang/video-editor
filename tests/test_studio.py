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


def test_three_tools_are_separate_pages(client: TestClient):
    home = client.get("/")
    assert home.status_code == 200
    assert "Edit the video" in home.text
    assert "Who the video is for" not in home.text
    pre = client.get("/pre")
    assert "Who the video is for" in pre.text
    assert "Pull from Cap" not in pre.text
    assert "Edit the video" not in pre.text
    image = client.get("/image")
    assert "Render image" in image.text
    assert "Suggest offers" not in image.text


def test_edit_starts_from_the_video(client: TestClient, tmp_path: Path):
    created = client.post("/api/edits")
    assert created.status_code == 200
    project_id = created.json()["id"]
    assert created.json()["kind"] == "edit"
    assert client.post(f"/api/projects/{project_id}/plan").status_code == 400
    camera = tmp_path / "camera.mp4"
    __import__("subprocess").run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(camera),
        ],
        check=True,
    )
    with camera.open("rb") as handle:
        uploaded = client.post(
            f"/api/projects/{project_id}/footage",
            files={"camera": ("camera.mp4", handle, "video/mp4")},
        )
    assert uploaded.status_code == 200
    priced = client.post(f"/api/projects/{project_id}/plan")
    assert priced.status_code == 200
    assert priced.json()["plan"]["mode"] == "edit"
    assert "Script and edit notes" not in {line["item"] for line in priced.json()["plan"]["quote"]["lines"]}


def test_image_renders_without_a_brief(client: TestClient, tmp_path: Path):
    face = tmp_path / "face.png"
    Image.new("RGB", (640, 480), (30, 30, 30)).save(face)
    with face.open("rb") as handle:
        rendered = client.post(
            "/api/images",
            data={"line1": "STOP", "highlight": "STOP", "accent": "yellow", "desaturate": "true"},
            files={"face": ("face.png", handle, "image/png")},
        )
    assert rendered.status_code == 200
    body = rendered.json()
    assert body["kind"] == "image"
    image = client.get(f"/api/projects/{body['id']}/thumbnail.jpg")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"


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


def test_folder_list_stops_at_forty_videos():
    from studio.cap import _MAX_VIDEOS, _extend

    videos = []
    seen = set()
    found = [{"id": f"v{index:04d}", "title": str(index), "url": f"https://cap.so/s/v{index:04d}"} for index in range(45)]
    assert _extend(videos, seen, found) < 0
    assert len(videos) == _MAX_VIDEOS


def test_cap_folder_lists_videos_in_page_order():
    from studio.cap import CapError, parse_collection_url, videos_from_html

    assert parse_collection_url("https://cap.so/c/folder1234?page=2") == "folder1234"
    page = """
    <meta property="og:title" content="Sales takes | Cap Collection">
    <a href="/s/aaaa1111"><div></div><h3>Intro</h3></a>
    <a href="/s/bbbb2222"><h3>Demo &amp; close</h3></a>
    """
    videos = videos_from_html(page)
    assert [video["id"] for video in videos] == ["aaaa1111", "bbbb2222"]
    assert videos[1]["title"] == "Demo & close"
    with pytest.raises(CapError):
        parse_collection_url("https://cap.so/s/aaaa1111")


def test_broll_locks_to_the_clap_that_sounds_like_it(tmp_path: Path):
    mic = tmp_path / "mic.wav"
    early = tmp_path / "early.wav"
    late = tmp_path / "late.wav"
    clip = tmp_path / "clip.wav"
    _tone(mic, [(1.0, 220), (7.0, 880)], seconds=10)
    _tone(early, [(0.4, 220)], seconds=3)
    _tone(late, [(0.4, 880)], seconds=3)
    _tone(clip, [(0.4, 880)], seconds=3)
    early_lock = sync.match_to_mic(mic, early)
    late_lock = sync.match_to_mic(mic, late)
    assert early_lock["ok"] is True
    assert early_lock["mic_start"] == pytest.approx(1.0 - 0.4, abs=0.05)
    assert late_lock["ok"] is True
    assert late_lock["mic_start"] == pytest.approx(7.0 - 0.4, abs=0.05)
    assert sync.match_to_mic(mic, clip)["mic_clap_at"] == pytest.approx(7.0, abs=0.05)


def test_broll_lands_inside_the_cap_order():
    from studio.classify import plan_sequence, role_from_title, suggest_role

    assert role_from_title("Cold intro") == "intro"
    assert role_from_title("Closing card") == "outro"
    assert suggest_role("Take 2", {"ok": True}) == "broll"
    assert suggest_role("Closing card", {"ok": False}) == "outro"
    caps = [
        {"role": "intro", "title": "Intro", "file": "i.mp4", "duration": 2},
        {"role": "screen", "title": "Demo", "file": "s.mp4", "duration": 4, "mic_start": 5},
        {"role": "outro", "title": "Outro", "file": "o.mp4", "duration": 2},
    ]
    brolls = [{"role": "broll", "title": "Take", "file": "b.mp4", "duration": 12, "mic_start": 0}]
    pieces = plan_sequence(caps, brolls)
    assert [piece["kind"] for piece in pieces] == ["intro", "face", "screen", "face", "outro"]
    assert pieces[1]["audio"] == "mic"
    assert pieces[1]["duration"] == 5
    assert pieces[2]["mic_at"] == 5
    assert pieces[3]["mic_at"] == 9
    loose = plan_sequence(
        [
            {"role": "intro", "title": "Intro", "file": "i.mp4", "duration": 2},
            {"role": "screen", "title": "Demo", "file": "s.mp4", "duration": 4},
            {"role": "outro", "title": "Outro", "file": "o.mp4", "duration": 2},
        ],
        [
            {"role": "broll", "title": "A", "file": "a.mp4", "duration": 3},
            {"role": "broll", "title": "B", "file": "b.mp4", "duration": 3},
        ],
    )
    assert [piece["kind"] for piece in loose] == ["intro", "face", "screen", "face", "outro"]


def test_folder_order_keeps_broll_on_the_mic(client: TestClient, monkeypatch):
    from studio import cap

    listing = {
        "id": "folder1234",
        "title": "Sales takes",
        "url": "https://cap.so/c/folder1234",
        "videos": [
            {"id": "intro1111", "title": "Intro", "url": "https://cap.so/s/intro1111"},
            {"id": "demo2222", "title": "Demo", "url": "https://cap.so/s/demo2222"},
        ],
        "truncated": False,
    }

    def fake_list(url: str) -> dict:
        return listing

    def fake_download(url: str, dest: Path) -> dict:
        dest.write_bytes(b"clip")
        return {"video_id": "x", "title": "", "url": url}

    def fake_probe(path: Path) -> float:
        name = Path(path).name
        if name.startswith("up"):
            return 12.0
        if "demo" in name:
            return 4.0
        return 2.0

    def fake_match(mic: Path, clip: Path) -> dict:
        name = Path(clip).name
        if name.startswith("up"):
            return {"ok": True, "mic_start": 0.0, "clip_clap_at": None, "mic_clap_at": 0.2, "confidence": 0.9, "reason": None}
        if "demo" in name:
            return {"ok": True, "mic_start": 5.0, "clip_clap_at": None, "mic_clap_at": 5.0, "confidence": 0.8, "reason": None}
        return {"ok": False, "reason": "intro stays at the front"}

    monkeypatch.setattr(cap, "list_collection", fake_list)
    monkeypatch.setattr(cap, "download", fake_download)
    monkeypatch.setattr("studio.main.sync.probe_duration", fake_probe)
    monkeypatch.setattr("studio.main.sync.match_to_mic", fake_match)
    created = client.post("/api/edits")
    project_id = created.json()["id"]
    mic = b"RIFFxxxxWAVEfmt "
    uploaded = client.post(
        f"/api/projects/{project_id}/footage",
        files=[
            ("mic", ("mic.wav", mic, "audio/wav")),
            ("clips", ("take.mp4", b"video", "video/mp4")),
        ],
    )
    assert uploaded.status_code == 200
    uploads = uploaded.json()["footage"]["uploads"]
    assert uploads[0]["suggested_role"] == "broll"
    pulled = client.post(
        f"/api/projects/{project_id}/cap-folder",
        json={
            "url": "https://cap.so/c/folder1234",
            "video_ids": ["intro1111", "demo2222"],
            "roles": {"intro1111": "intro", "demo2222": "screen"},
            "broll_ids": ["up0"],
        },
    )
    assert pulled.status_code == 200, pulled.text
    footage = pulled.json()["footage"]
    assert [piece["kind"] for piece in footage["sequence"]] == ["intro", "face", "screen", "face"]
    assert footage["sequence"][2]["audio"] == "mic"
    assert "locks to the mic" in footage["sequence_note"]


def test_folder_order_is_the_edit_order(client: TestClient, monkeypatch, tmp_path: Path):
    from studio import cap

    listing = {
        "id": "folder1234",
        "title": "Sales takes",
        "url": "https://cap.so/c/folder1234",
        "videos": [
            {"id": "aaaa1111", "title": "Intro", "url": "https://cap.so/s/aaaa1111"},
            {"id": "bbbb2222", "title": "Demo", "url": "https://cap.so/s/bbbb2222"},
        ],
        "truncated": False,
    }
    seen = []

    def fake_list(url: str) -> dict:
        assert url == "https://cap.so/c/folder1234"
        return listing

    def fake_download(url: str, dest: Path) -> dict:
        seen.append(url)
        dest.write_bytes(b"clip")
        return {"video_id": "x", "title": "", "url": url}

    def fake_stitch(paths, dest: Path) -> None:
        assert [path.name for path in paths] == ["clip-bbbb2222.mp4", "clip-aaaa1111.mp4"]
        dest.write_bytes(b"joined")

    monkeypatch.setattr(cap, "list_collection", fake_list)
    monkeypatch.setattr(cap, "download", fake_download)
    monkeypatch.setattr(cap, "stitch", fake_stitch)
    monkeypatch.setattr("studio.main.sync.probe_duration", lambda path: 12.0)
    created = client.post("/api/edits")
    project_id = created.json()["id"]
    pulled = client.post(
        f"/api/projects/{project_id}/cap-folder",
        json={"url": "https://cap.so/c/folder1234", "video_ids": ["bbbb2222", "aaaa1111"]},
    )
    assert pulled.status_code == 200
    footage = pulled.json()["footage"]
    assert [clip["title"] for clip in footage["clips"]] == ["Demo", "Intro"]
    assert footage["camera"] == "camera.mp4"
    assert seen == ["https://cap.so/s/bbbb2222", "https://cap.so/s/aaaa1111"]


def test_cap_share_url_must_be_a_public_cap_link():
    from studio.cap import CapError, parse_share_url

    assert parse_share_url("https://cap.so/s/zk5z77w95w8qctf") == "zk5z77w95w8qctf"
    assert parse_share_url("https://www.cap.so/embed/zk5z77w95w8qctf?t=1") == "zk5z77w95w8qctf"
    with pytest.raises(CapError):
        parse_share_url("https://example.com/s/zk5z77w95w8qctf")
    with pytest.raises(CapError):
        parse_share_url("http://cap.so/s/zk5z77w95w8qctf")


def test_cap_download_follows_the_video_redirect(tmp_path: Path):
    import httpx

    from studio.cap import download

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/playlist":
            return httpx.Response(302, headers={"location": "https://v.cap.so/owner/video/result.mp4"})
        if request.url.host == "v.cap.so":
            return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"\x00\x00\x00\x18ftypmp42" + b"x" * 2000)
        if request.url.path.startswith("/s/"):
            html = '<meta property="og:title" content="Demo take | Cap Recording">'
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    dest = tmp_path / "camera.mp4"
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    class _Client(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(httpx, "Client", _Client)
    try:
        info = download("https://cap.so/s/zk5z77w95w8qctf", dest)
    finally:
        monkey.undo()
    assert info["title"] == "Demo take"
    assert dest.stat().st_size > 1000


def test_desk_pulls_a_cap_into_the_project(client: TestClient, monkeypatch, tmp_path: Path):
    from studio import cap

    def fake_download(url: str, dest: Path) -> dict:
        dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 2000)
        return {"video_id": "zk5z77w95w8qctf", "title": "Demo take", "url": "https://cap.so/s/zk5z77w95w8qctf"}

    monkeypatch.setattr(cap, "download", fake_download)
    created = client.post("/api/projects", json=brief())
    project_id = created.json()["id"]
    pulled = client.post(
        f"/api/projects/{project_id}/cap",
        json={"url": "https://cap.so/s/zk5z77w95w8qctf", "slot": "camera"},
    )
    assert pulled.status_code == 200
    footage = pulled.json()["footage"]
    assert footage["camera"] == "camera.mp4"
    assert footage["cap_title"] == "Demo take"
    assert (store.ROOT / project_id / "camera.mp4").is_file()


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
    _tone(path, [(spike_at, 220)], rate=rate, seconds=seconds)


def _tone(path: Path, spikes: list[tuple[float, float]], rate: int = 8000, seconds: int = 5) -> None:
    frames = bytearray()
    for index in range(rate * seconds):
        moment = index / rate
        freq = 220.0
        sample = 800
        for spike_at, tone in spikes:
            if moment >= spike_at:
                freq = tone
            if abs(moment - spike_at) < 0.004:
                sample = 32000
                break
        else:
            sample = int(800 * math.sin(2 * math.pi * freq * moment))
        frames += struct.pack("<h", max(-32767, min(32767, sample)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
