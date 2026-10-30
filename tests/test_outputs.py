"""One folder per video: Upload/ for YouTube, Working/ for everything else. No network or ffmpeg."""
import json
import sys

import pytest

import ai_visuals as av
import make_video as mv
import outputs


def test_tee_copies_prints_to_the_log_and_restores_the_console(tmp_path, capsys):
    log = tmp_path / "Working" / "log.txt"
    with outputs.Tee(log):
        print("Rendering horizontal")
    print("after")
    assert sys.stdout is not None and "Rendering horizontal" in log.read_text(encoding="utf-8")
    assert "after" not in log.read_text(encoding="utf-8")
    assert "Rendering horizontal" in capsys.readouterr().out
    with outputs.Tee(log):
        print("second run")
    assert log.read_text(encoding="utf-8").count("=====") == 2  # runs are appended, each with a header


def test_image_check_report_lists_each_scene_s_verdicts(tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    av.save_verdict(img, {"ok": False, "problems": ["extra finger"], "score": 4})
    scenes = [{"srcs": {"horizontal": img, "vertical": tmp_path / "unchecked.jpg"}}]
    rows = json.loads(av.write_check_report(scenes, tmp_path / "checks.json").read_text(encoding="utf-8"))["checks"]
    assert rows == [{"scene": 1, "format": "horizontal", "image": str(img), "ok": False, "problems": ["extra finger"],
                     "score": 4}]


def test_chatterbox_kaggle_job_goes_in_the_video_s_working_folder(tmp_path, monkeypatch):
    import chatterbox_tts as cb
    from test_chatterbox import notebook, settings
    from test_kaggle_runner import FakeCLI
    monkeypatch.setattr(cb, "VOICE_DIR", tmp_path / "voice")
    monkeypatch.setattr(cb, "WORK", tmp_path / "voice" / "_kaggle")
    work = tmp_path / "Exports" / "V" / "Working" / "kaggle" / "chatterbox"

    def output(dest):  # the fake notebook reads the job from the work folder it was given
        monkeypatch.setattr(cb, "WORK", work)
        notebook()(dest)
    paths, _ = cb.synthesize([("One line.", settings())], cli=FakeCLI(output=output), sleep=lambda s: None,
                             clock=iter(range(0, 10**6, 60)).__next__, work=work)
    assert (work / "job" / "job.json").exists() and paths[0].parent == tmp_path / "voice"  # audio in the shared cache


def test_a_run_makes_the_upload_and_working_folders_and_logs_there(tmp_path, monkeypatch):
    import remotion_render as rr
    import stock
    p = tmp_path / "My Video.txt"
    p.write_text("# Visuals: a dusty road\nNo fridge here.\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(mv, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(rr, "available", lambda: (True, ""))
    monkeypatch.setattr(av, "Providers", lambda *a, **k: None)
    monkeypatch.setattr(stock, "fill_stock", lambda *a, **k: {})
    monkeypatch.setattr(stock, "write_media_json", lambda scenes, plan, path: seen.setdefault("media", path))
    monkeypatch.setattr(av, "generate_scene_visuals", lambda scenes, prompts, **k: seen.setdefault("prompts", prompts) and [])
    monkeypatch.setattr(outputs, "EXPORTS", tmp_path / "Exports")
    monkeypatch.setattr(mv.sys, "argv", ["make_video.py", str(p), "--visuals-only"])
    mv.main()
    folder = tmp_path / "Exports" / "My Video"
    assert (folder / "Upload").is_dir() and (folder / "Working").is_dir()
    assert seen == {"prompts": folder / "Working" / "prompts.json", "media": folder / "Working" / "media.json"}
    assert "Visuals only" in (folder / "Working" / "log.txt").read_text(encoding="utf-8")
