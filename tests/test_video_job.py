"""Tests for [video] scenes, Kaggle job export, clip import and clip-fill timing.
No GPU, ffmpeg or network: every Cloudflare call is mocked."""
import base64
import io
import json
import zipfile

import pytest
from PIL import Image

import ai_visuals as av
import make_video as mv
import video_job as vj
from video_job import Segment

CREDS = ("acct", "tok")


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(av, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    monkeypatch.setattr(av.time, "sleep", lambda s: None)
    for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "POLLINATIONS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def cf(monkeypatch):
    """Fake Workers AI: records calls, answers text and image requests."""
    calls = []

    def run(model, payload, creds, timeout=120):
        calls.append((model, payload))
        if model == av.TEXT_MODEL:
            system = payload["messages"][0]["content"]
            kind = "motion" if system == vj.MOTION_SYSTEM else "image"
            return {"response": f"{kind} prompt {len(calls)}"}
        return {"image": base64.b64encode(jpeg_bytes()).decode()}
    monkeypatch.setattr(av, "cf_run", run)
    return calls


def motion_calls(calls):
    return [p for m, p in calls if m == av.TEXT_MODEL and p["messages"][0]["content"] == vj.MOTION_SYSTEM]


def script(tmp_path, text):
    p = tmp_path / "s.txt"
    p.write_text(text, encoding="utf-8")
    return mv.parse_script(p)


DEMO = ("# hook shot\n[video]\nA clay pot in the sun.\n\n"
        "Fog rolls over the hills.\n\n"
        "[VIDEO]\nA root bridge over a river.\n")


# ---------- marking scenes ----------

def test_video_tag_is_parsed_and_not_spoken(tmp_path):
    sc = script(tmp_path, DEMO)
    assert [s["video"] for s in sc] == [True, False, True]
    assert sc[0]["text"] == "A clay pot in the sun." and "notes" not in sc[0]


def test_video_scenes_option_marks_scenes(tmp_path):
    sc = script(tmp_path, DEMO)
    vj.apply_video_scenes(sc, vj.parse_scene_numbers(" 2, 2,3 "))
    assert [s["video"] for s in sc] == [True, True, True]


def test_visual_scenes_are_never_animated(tmp_path, capsys):
    (tmp_path / "mine.png").write_bytes(jpeg_bytes())
    sc = script(tmp_path, "[visual: mine.png]\n[video]\nMy footage.\n")
    vj.apply_video_scenes(sc, [])
    assert sc[0]["video"] is False and "won't be animated" in capsys.readouterr().out


def test_bad_scene_numbers_exit(tmp_path):
    sc = script(tmp_path, DEMO)
    with pytest.raises(SystemExit):
        vj.apply_video_scenes(sc, [4])
    with pytest.raises(SystemExit):
        vj.parse_scene_numbers("3,seven")


# ---------- export ----------

def export(tmp_path, sc, mode="cloudflare"):
    mv.assign_visuals(sc, tmp_path, use_pool=False)
    providers = av.Providers(mode, creds=CREDS)
    return vj.export_job(sc, tmp_path / "Exports" / "demo", providers)


def test_export_writes_job_json_images_and_zip(tmp_path, cf):
    zip_path = export(tmp_path, script(tmp_path, DEMO))
    job_dir = tmp_path / "Exports" / "demo_video_job"
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert [e["scene"] for e in job["scenes"]] == [1, 3]  # image-only scene 2 is made locally
    assert job["video_size"] == [960, 544]
    first = job["scenes"][0]
    assert first["task"] == "video" and first["image"] == "scene_001.jpg"
    assert first["motion_prompt"].startswith("motion prompt") and first["seconds"] == vj.CLIP_SECONDS
    assert first["seed"] == av.stable_seed("A clay pot in the sun.")
    assert first["image_prompt"].endswith(av.COMPOSITION)
    with zipfile.ZipFile(zip_path) as z:
        assert sorted(z.namelist()) == ["job.json", "scene_001.jpg", "scene_003.jpg"]
    # only the two video scenes' images were generated
    assert sum(m == av.IMAGE_MODEL for m, _ in cf) == 2


def test_motion_prompt_edits_are_reused_on_reexport(tmp_path, cf):
    export(tmp_path, script(tmp_path, DEMO))
    job_json = tmp_path / "Exports" / "demo_video_job" / "job.json"
    job = json.loads(job_json.read_text(encoding="utf-8"))
    job["scenes"][0]["motion_prompt"] = "slow push-in, heat haze shimmering"
    job_json.write_text(json.dumps(job), encoding="utf-8")
    cf.clear()

    export(tmp_path, script(tmp_path, DEMO))
    job = json.loads(job_json.read_text(encoding="utf-8"))
    assert job["scenes"][0]["motion_prompt"] == "slow push-in, heat haze shimmering"
    assert motion_calls(cf) == []  # both motion prompts came from the saved job
    assert not any(m == av.IMAGE_MODEL for m, _ in cf)  # and both images from the cache


def test_export_without_video_scenes_exits(tmp_path, cf):
    with pytest.raises(SystemExit):
        export(tmp_path, script(tmp_path, "Just narration.\n"))


def test_kaggle_export_asks_notebook_for_missing_images(tmp_path, cf):
    zip_path = export(tmp_path, script(tmp_path, DEMO), mode="kaggle")
    job = json.loads((tmp_path / "Exports" / "demo_video_job" / "job.json").read_text(encoding="utf-8"))
    assert [(e["scene"], e["task"], e["image"]) for e in job["scenes"]] == [
        (1, "video", None), (2, "image", None), (3, "video", None)]
    assert not any(m == av.IMAGE_MODEL for m, _ in cf)  # nothing generated online
    with zipfile.ZipFile(zip_path) as z:
        assert z.namelist() == ["job.json"]


# ---------- import ----------

def make_clips_zip(tmp_path, items, files):
    zip_path = tmp_path / "clips.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("manifest.json", json.dumps({"items": items}))
        for name, data in files.items():
            z.writestr(name, data)
    return zip_path


def test_import_matches_clips_by_narration_and_caches_images(tmp_path):
    sc = script(tmp_path, DEMO)
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, "PNG")
    prompt = av.full_prompt("fog over hills", "")
    zip_path = make_clips_zip(tmp_path, [
        {"scene": 9, "narration": "A root bridge over a river.", "kind": "video", "file": "clips/scene_003.mp4",
         "model": "Wan2.2-TI2V-5B", "width": 1280, "height": 704},
        {"scene": 2, "narration": "Fog rolls over the hills.", "kind": "image", "file": "images/scene_002.png",
         "prompt": prompt},
    ], {"clips/scene_003.mp4": b"mp4", "images/scene_002.png": png.getvalue()})

    assert vj.import_clips(zip_path, sc, tmp_path / "work") == (1, 1)
    assert sc[2]["clip"].name == "scene_003.mp4" and "clip" not in sc[0]
    cached = av.image_path(prompt, av.KAGGLE_CACHE_ID)
    assert cached.exists() and av.Providers("kaggle", creds=CREDS).cached(prompt)[1] == "kaggle"


def test_import_skips_missing_files_changed_narration_and_visual_scenes(tmp_path, capsys):
    (tmp_path / "mine.png").write_bytes(jpeg_bytes())
    sc = script(tmp_path, "[visual: mine.png]\nMy footage.\n\nA clay pot in the sun.\n")
    zip_path = make_clips_zip(tmp_path, [
        {"narration": "My footage.", "kind": "video", "file": "clips/a.mp4"},
        {"narration": "An edited sentence.", "kind": "video", "file": "clips/b.mp4"},
        {"narration": "A clay pot in the sun.", "kind": "video", "file": "clips/missing.mp4"},
    ], {"clips/a.mp4": b"x", "clips/b.mp4": b"x"})
    assert vj.import_clips(zip_path, sc, tmp_path / "work") == (0, 0)
    out = capsys.readouterr().out
    assert "[visual:] line" in out and "no scene matches" in out and "missing" in out


def test_import_falls_back_to_scene_number_without_narration(tmp_path):
    sc = script(tmp_path, DEMO)
    folder = tmp_path / "clips_folder"
    (folder / "clips").mkdir(parents=True)
    (folder / "clips" / "c.mp4").write_bytes(b"x")
    (folder / "manifest.json").write_text(json.dumps({"items": [
        {"scene": 1, "kind": "video", "file": "clips/c.mp4", "width": 832, "height": 480}]}))
    assert vj.import_clips(folder, sc, tmp_path / "work") == (1, 0)
    assert sc[0]["clip"].name == "c.mp4"


def test_scene_without_clip_falls_back_to_its_image(tmp_path, cf):
    sc = script(tmp_path, DEMO)
    mv.assign_visuals(sc, tmp_path, use_pool=False)
    zip_path = make_clips_zip(tmp_path, [
        {"narration": "A clay pot in the sun.", "kind": "video", "file": "clips/1.mp4"}], {"clips/1.mp4": b"x"})
    vj.import_clips(zip_path, sc, tmp_path / "work")
    only = {i for i, s in enumerate(sc) if not s.get("clip")}  # what make_video passes
    av.generate_scene_visuals(sc, tmp_path / "p.json", providers=av.Providers("cloudflare", creds=CREDS), only=only)
    assert sc[0].get("clip") and sc[0]["src"] is None  # clip scene: no image spent on it
    assert sc[2]["video"] and "clip" not in sc[2] and sc[2]["src"].exists()  # no clip came back: still image


def test_missing_manifest_exits(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit):
        vj.import_clips(tmp_path / "empty", [], tmp_path / "work")


# ---------- clip fill timing ----------

def total(plan):
    return sum(s.seconds for s in plan)


def test_clip_longer_than_scene_is_just_trimmed():
    assert vj.clip_fill_plan(5.0, 3.2) == [Segment("forward", 1.0, 3.2)]


def test_pingpong_alternates_and_fills_exactly():
    plan = vj.clip_fill_plan(5.0, 17.0, "pingpong")
    assert [s.kind for s in plan] == ["forward", "reverse", "forward", "reverse"]
    assert [s.stretch for s in plan] == [1.0] * 4
    assert total(plan) == pytest.approx(17.0) and plan[-1].seconds == pytest.approx(2.0)


def test_slowmo_fills_remainder_with_one_slow_reverse_pass():
    plan = vj.clip_fill_plan(5.0, 12.0, "slowmo")
    assert plan == [Segment("forward", 1.0, 5.0), Segment("reverse", 1.4, 7.0)]


def test_slowmo_never_speeds_up_short_remainders():
    plan = vj.clip_fill_plan(5.0, 7.0, "slowmo")
    assert plan[1] == Segment("reverse", 1.0, 2.0)


def test_slowmo_caps_slowdown_and_alternates_beyond_it():
    plan = vj.clip_fill_plan(5.0, 40.0, "slowmo")
    assert plan[0] == Segment("forward", 1.0, 5.0)
    assert all(s.stretch == vj.MAX_SLOWMO for s in plan[1:])
    assert [s.kind for s in plan[1:]] == ["reverse", "forward", "reverse"]
    assert total(plan) == pytest.approx(40.0)


def test_freeze_holds_last_frame():
    assert vj.clip_fill_plan(5.0, 9.5, "freeze") == [Segment("forward", 1.0, 5.0), Segment("hold", 1.0, 4.5)]


def test_bad_fill_inputs():
    with pytest.raises(ValueError):
        vj.clip_fill_plan(5.0, 9.0, "bounce")
    with pytest.raises(ValueError):
        vj.clip_fill_plan(0, 9.0)


def test_clip_filter_graph():
    fit = "scale=1920:1080"
    g = vj.clip_filter(vj.clip_fill_plan(5.0, 12.0, "slowmo"), fit)
    assert "split=2[s0][s1]" in g and "[s1]reverse,setpts=1.4000*(PTS-STARTPTS)" in g
    assert "concat=n=2:v=1:a=0,framerate=fps=30[v]" in g
    g = vj.clip_filter(vj.clip_fill_plan(5.0, 9.0, "freeze"), fit)
    assert "split" not in g and "tpad=stop_mode=clone:stop_duration=4.000" in g and g.endswith("fps=30[v]")


def test_kaggle_notebook_matches_its_py_and_compiles():
    import sys
    from pathlib import Path
    kaggle = Path(mv.ROOT) / "kaggle"
    sys.path.insert(0, str(kaggle))
    import build_notebook
    py = (kaggle / "wan_video.py").read_text(encoding="utf-8")
    compile(py, "wan_video.py", "exec")  # syntax only; the notebook needs a GPU to run
    nb = json.loads((kaggle / "wan_video.ipynb").read_text(encoding="utf-8"))
    assert nb["cells"] == build_notebook.cells_from_py(py), "run kaggle/build_notebook.py"
    assert nb["cells"][0]["cell_type"] == "markdown" and "GPU T4 x2" in "".join(nb["cells"][0]["source"])


def test_cli_options():
    a = mv.parse_args(["s.txt"])
    assert (a.clip_fill, a.image_provider, a.text_model, a.export_video_job) == \
        ("pingpong", "auto", av.TEXT_MODEL, False)
    a = mv.parse_args(["s.txt", "--video-scenes", "3,7", "--clip-fill", "freeze", "--image-provider", "kaggle",
                       "--text-model", "@cf/x", "--export-video-job", "--clips", "c.zip"])
    assert (a.video_scenes, a.clip_fill, a.image_provider, a.text_model, a.export_video_job, a.clips.name) == \
        ("3,7", "freeze", "kaggle", "@cf/x", True, "c.zip")
