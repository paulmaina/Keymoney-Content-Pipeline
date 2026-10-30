"""Draft and final renders: where each goes, the DRAFT label, what a draft skips, and the brainstorming commands
(--thumbnail-ideas, --style-test). Pollinations, Cloudflare and the renderers are faked."""
import base64
import shutil
import subprocess

import pytest
from PIL import Image

import ai_visuals as av
import brainstorm
import make_video as mv
import outputs
import remotion_render as rr
import upscale
from test_ai_visuals import CREDS, jpeg_b64


@pytest.fixture
def story(tmp_path, monkeypatch):
    """A three-scene script and a render pipeline that records what it was asked to do."""
    p = tmp_path / "Story.txt"
    p.write_text("\n\n".join(f"# Visuals: scene {n}\nScene number {n} is here." for n in range(1, 4)), encoding="utf-8")
    seen = {"providers": []}
    monkeypatch.setattr(outputs, "EXPORTS", tmp_path / "Exports")
    monkeypatch.setattr(mv, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(rr, "available", lambda: (True, ""))
    for key, value in (("CLOUDFLARE_ACCOUNT_ID", CREDS[0]), ("CLOUDFLARE_API_TOKEN", CREDS[1]),
                       ("POLLINATIONS_API_KEY", "sk_test")):  # the keys the early checks look for
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(mv, "narrate", lambda sc, work, args: work / "voice.wav")
    monkeypatch.setattr(mv, "make_captions", lambda *a, **k: (None, None))
    monkeypatch.setattr(mv, "duration", lambda path: 12.0)
    monkeypatch.setattr(mv, "stamp_draft", lambda video, font: seen.setdefault("label", video))
    monkeypatch.setattr(upscale, "upscale_scenes", lambda *a, **k: seen.setdefault("upscaled", True))

    class Providers:
        def __init__(self, mode, *a, **k):
            self.mode, self.cap, self.backup = mode, k.get("cap"), {}
            seen["providers"].append(self)

    def visuals(scenes, prompts, providers=None, check=False, **k):
        seen["check"] = check
        if providers.mode == "final":
            providers.backup[2] = "Pollinations backup image"
        return []

    def render_all(sc, work, narration, chunks, font, args, formats, out, script, parts, checkpoints=None):
        seen.update(formats=formats, video=out.video(formats[0]), parts=parts)
        return [out.video(f) for f in formats], 0.0, 12.0
    monkeypatch.setattr(av, "Providers", Providers)
    monkeypatch.setattr(av, "generate_scene_visuals", visuals)
    monkeypatch.setattr(rr, "render_all", render_all)
    import stock
    monkeypatch.setattr(stock, "fill_stock", lambda *a, **k: {})
    monkeypatch.setattr(stock, "write_media_json", lambda *a, **k: seen.setdefault("media_json", True))
    return p, seen


def run(monkeypatch, *argv):
    monkeypatch.setattr(mv.sys, "argv", ["make_video.py", *map(str, argv)])
    mv.main()


def test_a_draft_goes_to_working_with_a_label_and_skips_the_final_steps(story, tmp_path, monkeypatch, capsys):
    p, seen = story
    run(monkeypatch, p, "--draft", "--engine", "edge", "--no-metadata")
    working = tmp_path / "Exports" / "smart_village_boy" / "Story" / "Working"
    assert seen["video"] == seen["label"] == working / "Story_DRAFT.mp4"
    assert seen["formats"] == ["horizontal"] and seen["parts"] == []  # one format, no Shorts
    assert seen["providers"][0].mode == "draft" and seen["check"] is False
    assert "upscaled" not in seen and "media_json" not in seen  # no Kaggle; the final render's records untouched
    assert not any((tmp_path / "Exports" / "smart_village_boy" / "Story" / "Upload").iterdir())
    out = capsys.readouterr().out
    assert f"DRAFT render: {working}" in out and "FINAL render" not in out and "aren't Cloudflare's work" not in out


def test_the_plain_command_is_a_final_render(story, tmp_path, monkeypatch, capsys):
    p, seen = story
    monkeypatch.setattr(mv, "make_captions", lambda *a, **k: (None, None))
    run(monkeypatch, p, "--engine", "edge", "--no-metadata", "--format", "horizontal")
    upload = tmp_path / "Exports" / "smart_village_boy" / "Story" / "Upload"
    assert seen["providers"][0].mode == "final" and seen["providers"][0].cap == av.MAX_DAILY_NEURONS
    assert seen["check"] is True and seen["upscaled"] and seen["media_json"] and "label" not in seen
    assert seen["video"] == upload / "Story.mp4"
    out = capsys.readouterr().out
    assert out.rstrip().endswith(f"FINAL render: ready to upload from {upload}")
    assert "scene 2: Pollinations backup image" in out and '--redo-scenes "2"' in out
    log = (tmp_path / "Exports" / "smart_village_boy" / "Story" / "Working" / "log.txt").read_text(encoding="utf-8")
    assert '--redo-scenes "2"' in log and "FINAL render" in log  # also in the log


def test_draft_images_in_the_project_defaults_make_every_run_a_draft(story, tmp_path, monkeypatch):
    p, seen = story
    cfg = tmp_path / "make_video.toml"
    cfg.write_text("[defaults]\ndraft-images = true\n", encoding="utf-8")
    monkeypatch.setattr(mv, "CONFIG_FILE", cfg)
    run(monkeypatch, p, "--engine", "edge", "--no-metadata")
    assert seen["providers"][0].mode == "draft" and seen["video"].name == "Story_DRAFT.mp4"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_draft_label_is_on_every_frame(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x204060:s=640x360:d=1:r=10",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", str(video)], check=True)
    mv.stamp_draft(video, mv.DEFAULT_FONT)
    for t in ("0", "0.9"):
        frame = tmp_path / f"f{t}.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", t, "-i", str(video), "-frames:v", "1", str(frame)],
                       check=True)
        with Image.open(frame) as img:
            r, g, b = img.convert("RGB").getpixel((640 - 40, 40))[:3]
            assert r > 120 and g < 90 and b < 90  # the red label in the top-right corner
            assert img.convert("RGB").getpixel((20, 340))[2] > 50  # the picture elsewhere is untouched
    assert "audio" in subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv",
                                      str(video)], capture_output=True, text=True).stdout  # the sound is kept


# ---------- brainstorming ----------

@pytest.fixture
def pollinations(monkeypatch, tmp_path):
    drawn = []

    def poll_image(prompt, key, seed, timeout=180, size="square"):
        drawn.append((prompt, seed, size))
        return base64.b64decode(jpeg_b64())
    monkeypatch.setattr(av, "pollinations_run", poll_image)
    monkeypatch.setattr(av, "pollinations_text_run", lambda messages, key, *a, **k: "A wide shot of a clay pot")
    monkeypatch.setattr(av, "pollinations_key", lambda: "sk_test")
    monkeypatch.setattr(av, "cf_run", lambda *a, **k: pytest.fail("brainstorming called Cloudflare"))
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    return drawn


def args_for(*argv):
    return mv.parse_args(["s.txt", *argv])


def test_thumbnail_ideas_are_n_different_pollinations_pictures(pollinations, tmp_path):
    script = tmp_path / "Clay Fridge.txt"
    script.write_text("No power, no fridge.\n\nA pot inside a pot keeps food cold.\n", encoding="utf-8")
    out = outputs.Outputs(tmp_path / "Exports" / "Clay Fridge").make()
    made = brainstorm.thumbnail_ideas(args_for("--thumbnail-ideas", "8"), script, mv.parse_script(script),
                                      mv.DEFAULT_FONT, out, brainstorm.providers_for(args_for()))
    assert [m.name for m in made] == [f"idea_{k}.jpg" for k in range(1, 9)]
    assert all(m.parent == out.working / "thumbnail_ideas" for m in made)
    assert len({p for p, _, _ in pollinations}) == len(brainstorm.THUMB_SHOTS)  # a different shot for each idea...
    assert len({(p, s) for p, s, _ in pollinations}) == 8  # ...then the same shots again with new seeds
    with Image.open(made[0]) as img:
        assert img.size == (1280, 720)
    assert all(f.parent.name == "drafts" for f in (av.GEN_DIR / "drafts").iterdir())  # the draft cache


def test_style_test_draws_one_scene_in_each_style(pollinations, tmp_path):
    script = tmp_path / "s.txt"
    script.write_text("[visual: mine.png]\nMine.\n\nA pot inside a pot.\n", encoding="utf-8")
    out = outputs.Outputs(tmp_path / "Exports" / "s").make()
    made = brainstorm.style_test(args_for("--style-test", "watercolour | 3D animated film |"), script,
                                 mv.parse_script(script), out, brainstorm.providers_for(args_for()))
    assert [m.name for m in made] == ["scene2_1_watercolour.jpg", "scene2_2_3d_animated_film.jpg"]  # first AI scene
    assert ", watercolour, " in pollinations[0][0] and ", 3D animated film, " in pollinations[1][0]
    with pytest.raises(SystemExit):
        brainstorm.style_test(args_for("--style-test", "a", "--style-test-scene", "9"), script,
                              mv.parse_script(script), out, brainstorm.providers_for(args_for()))


def test_brainstorming_stops_before_any_voice_or_video(pollinations, tmp_path, monkeypatch):
    p = tmp_path / "s.txt"
    p.write_text("A pot inside a pot.\n", encoding="utf-8")
    monkeypatch.setattr(outputs, "EXPORTS", tmp_path / "Exports")
    monkeypatch.setattr(mv, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(mv, "produce", lambda *a, **k: pytest.fail("rendered"))
    run(monkeypatch, p, "--thumbnail-ideas", "2", "--style-test", "ink sketch")
    working = tmp_path / "Exports" / "smart_village_boy" / "s" / "Working"
    assert len(list((working / "thumbnail_ideas").iterdir())) == 2
    assert [f.name for f in (working / "style_tests").iterdir()] == ["scene1_1_ink_sketch.jpg"]


def test_a_draft_without_a_pollinations_key_explains_the_setup(monkeypatch):
    monkeypatch.setattr(av, "pollinations_key", lambda: None)
    with pytest.raises(SystemExit) as e:
        av.Providers("draft", creds=CREDS)
    assert "POLLINATIONS_API_KEY" in str(e.value) and "enter.pollinations.ai" in str(e.value)
