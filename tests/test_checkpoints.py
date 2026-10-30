"""Resumable final renders (checkpoints.py): a failed render picks up from its last finished stage. No network or FFmpeg."""
import json
from types import SimpleNamespace

import pytest

import checkpoints
import make_video as mv
import outputs
import upscale


def test_same_key_resumes_a_new_key_or_fresh_starts_over(tmp_path, capsys):
    work = tmp_path / "render"
    done = checkpoints.Checkpoints(work, "k1")
    done.save("narration", [1.5, 2.0])
    (work / "narration.wav").write_bytes(b"x")
    again = checkpoints.Checkpoints(work, "k1")
    assert again.get("narration") == [1.5, 2.0] and (work / "narration.wav").exists()
    assert "Resuming the last render" in capsys.readouterr().out
    changed = checkpoints.Checkpoints(work, "k2")
    assert changed.get("narration") is None and not (work / "narration.wav").exists()
    assert "starting over" in capsys.readouterr().out
    changed.save("captions", [])
    assert checkpoints.Checkpoints(work, "k2", fresh=True).get("captions") is None


def test_the_key_follows_the_script_pictures_and_settings_not_bookkeeping_options(tmp_path):
    script = tmp_path / "s.txt"
    script.write_text("Hello.\n", encoding="utf-8")
    pic = tmp_path / "a.png"
    pic.write_bytes(b"1")
    scenes = [{"text": "Hello.", "src": pic}]
    args = SimpleNamespace(rate="+20%", voice="am_michael", keep_work=False, fresh=False)
    key = checkpoints.render_key(script, scenes, args)
    assert checkpoints.render_key(script, scenes, SimpleNamespace(**{**vars(args), "keep_work": True})) == key
    assert checkpoints.render_key(script, scenes, SimpleNamespace(**{**vars(args), "rate": "+15%"})) != key
    pic.write_bytes(b"22")  # a redrawn picture
    assert checkpoints.render_key(script, scenes, args) != key


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """A final render with the ffmpeg renderer whose expensive steps are recorded instead of run."""
    calls = []
    monkeypatch.setattr(outputs, "EXPORTS", tmp_path / "Exports")
    monkeypatch.setattr(mv, "FOOTAGE_DIRS", [])
    monkeypatch.setattr(mv, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(mv, "duration", lambda p: 3.0)
    monkeypatch.setattr(upscale, "upscale_scenes", lambda *a, **k: None)

    def narrate(scenes, work, args):
        calls.append("voice")
        for s in scenes:
            s["dur"] = 3.0
        (work / "narration.wav").write_bytes(b"wav")
        return work / "narration.wav"

    def captions(audio, out, text, model, words):
        calls.append("captions")
        out.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n", encoding="utf-8")
        return out, [[mv.Word("Hello", 0.0, 1.0)]]

    def render_format(fmt, scenes, work, narration, srt, font, args, out):
        calls.append(fmt)
        if fmt == "vertical" and state["fail"]:
            raise RuntimeError("the vertical render crashed")
        out.write_bytes(b"mp4")
        return out

    state = {"fail": True}
    monkeypatch.setattr(mv, "narrate", narrate)
    monkeypatch.setattr(mv, "make_captions", captions)
    monkeypatch.setattr(mv, "render_format", render_format)
    script = tmp_path / "Story.txt"
    script.write_text("# A Story About Water\n\nThe river is dry.\n\nThe well is full.\n", encoding="utf-8")

    def run(*extra):
        monkeypatch.setattr(mv.sys, "argv", ["make_video.py", str(script), "--no-generate-visuals", "--no-metadata",
                                             "--renderer", "ffmpeg", *extra])
        mv.main()
    return SimpleNamespace(calls=calls, state=state, run=run, script=script,
                           work=tmp_path / "Exports" / "smart_village_boy" / "Story" / "Working" / "render")


def test_a_failed_render_resumes_from_its_last_good_stage(pipeline):
    with pytest.raises(RuntimeError):
        pipeline.run()
    assert pipeline.calls == ["voice", "captions", "horizontal", "vertical"]
    stages = json.loads((pipeline.work / "checkpoints.json").read_text(encoding="utf-8"))["stages"]
    assert set(stages) == {"narration", "captions", "render_horizontal"}
    pipeline.calls.clear()
    pipeline.state["fail"] = False
    pipeline.run()
    assert pipeline.calls == ["vertical"]  # the voice, captions and horizontal video came from the last run
    assert not pipeline.work.exists()  # a successful final render cleans its checkpoints up


def test_fresh_or_an_edited_script_starts_over(pipeline):
    with pytest.raises(RuntimeError):
        pipeline.run()
    pipeline.calls.clear()
    with pytest.raises(RuntimeError):
        pipeline.run("--fresh")
    assert pipeline.calls == ["voice", "captions", "horizontal", "vertical"]
    pipeline.calls.clear()
    pipeline.script.write_text("# A Story About Water\n\nThe river is dry today.\n\nThe well is full.\n", encoding="utf-8")
    pipeline.state["fail"] = False
    pipeline.run("--keep-work")
    assert pipeline.calls == ["voice", "captions", "horizontal", "vertical"]
    assert (pipeline.work / "checkpoints.json").exists()  # --keep-work keeps them


def test_remotion_reuses_prepared_pictures_the_bundle_and_finished_renders(tmp_path, monkeypatch):
    import captions
    import remotion_render as rr
    p = tmp_path / "s.txt"
    p.write_text("# HOOK\nNo fridge here.\n\n# HACK 1\nSand holds water.\n", encoding="utf-8")
    scenes = mv.parse_script(p)
    for s in scenes:
        s["dur"] = 5.0
    prepared, npx_calls = [], []
    monkeypatch.setattr(rr, "prepare_assets", lambda sc, *a, **k: prepared.append(1) or [{"kind": "card"} for _ in sc])
    monkeypatch.setattr(rr, "save_speed", lambda *a: None)
    monkeypatch.setattr(captions, "scene_faces", lambda sc, fmt, **kw: [[] for _ in sc])
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: "")
    monkeypatch.setattr(mv, "mux", lambda video, audio, args, out, **k: out)

    def npx(*a, **k):
        npx_calls.append(a[1])
        if a[1] == "bundle":
            (tmp_path / "work" / "remotion" / "bundle").mkdir(parents=True, exist_ok=True)
            (tmp_path / "work" / "remotion" / "bundle" / "index.html").write_text("x")
        if a[1] == "render":
            silent = mv.Path(a[4])
            silent.write_bytes(b"mp4")
            if silent.name.startswith("vertical"):
                raise SystemExit("Remotion failed")
    monkeypatch.setattr(rr, "npx", npx)
    args = mv.parse_args([str(p), "--no-parallax"])
    font = tmp_path / "f.ttf"
    font.write_bytes(b"x")
    out = outputs.Outputs(tmp_path / "vid").make()

    def render():
        done = checkpoints.Checkpoints(tmp_path / "work", "same key")
        return rr.render_all(scenes, tmp_path / "work", tmp_path / "narration.wav", None, font, args,
                             ["horizontal", "vertical"], out, p, parts=[(0, 2)], checkpoints=done)
    with pytest.raises(SystemExit):
        render()
    assert (prepared, npx_calls) == ([1, 1], ["bundle", "render", "render"])
    prepared.clear()
    npx_calls.clear()
    with pytest.raises(SystemExit):
        render()
    assert (prepared, npx_calls) == ([], ["render"])  # only the vertical video, which failed, is rendered again
