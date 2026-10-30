"""An offline end-to-end final render with the real FFmpeg and Remotion: synthetic narration, local pictures, the
channel's own branding clips, music, captions and the Upload manifest. No network calls or keys: the voice, Whisper,
the face detector and Kaggle upscaling are replaced by local stand-ins.

Slow (about a minute and a half), so it only runs with SMOKE_RENDER=1 (CI's smoke job sets it):
    set SMOKE_RENDER=1 & .venv\\Scripts\\python -m pytest -q tests\\test_smoke_render.py
"""
import json
import os
import re
import shutil
import subprocess

import pytest

import channels
import make_video as mv
import outputs
import remotion_render as rr

REAL_INSTALL = rr.ensure_installed  # conftest stubs it for the unit tests; this test installs for real

pytestmark = pytest.mark.skipif(not os.environ.get("SMOKE_RENDER"), reason="slow: set SMOKE_RENDER=1")


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    return (video["width"], video["height"], float(data["format"]["duration"]),
            any(s["codec_type"] == "audio" for s in data["streams"]))


def srt_starts(path):
    return [int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
            for h, m, s, ms in re.findall(r"^(\d\d):(\d\d):(\d\d),(\d\d\d) -->", path.read_text(encoding="utf-8"), re.M)]


def loudness(path, start, end):
    out = subprocess.run(["ffmpeg", "-v", "info", "-ss", str(start), "-to", str(end), "-i", str(path), "-vn", "-af",
                          "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", out).group(1))


def test_offline_final_render_end_to_end(tmp_path, monkeypatch):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("needs FFmpeg")
    ok, why = rr.available()
    if not ok:
        pytest.skip(why)
    monkeypatch.setattr(rr, "ensure_installed", REAL_INSTALL)
    from PIL import Image
    import captions
    import upscale

    # a channel with a 2 s horizontal intro (with sound) and a vertical intro of 1 s of picture and 3 s of sound
    branding = channels.CHANNELS_DIR / "smart_village_boy" / "branding"
    branding.mkdir(parents=True)
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=2", "-f", "lavfi", "-i",
           "sine=frequency=660:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
           branding / "intro_horizontal.mp4")
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=1", "-f", "lavfi", "-i",
           "sine=frequency=660:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           branding / "intro_vertical.mp4")
    (branding / "outro_vertical_TEMPLATE.mp4").write_bytes(b"never used")

    # a three-scene script with local pictures, music, and a stale Short part from an earlier render
    for k, colour in enumerate([(200, 80, 40), (40, 120, 200), (60, 160, 80)], 1):
        Image.new("RGB", (1600, 1600), colour).save(tmp_path / f"pic{k}.png")
    script = tmp_path / "Story.txt"
    script.write_text("# A Story About Water\n# channel: smart_village_boy\n\n"
                      "# HOOK\n[visual: pic1.png] The river is dry.\n\n"
                      "# HACK 1 KENYA\n[visual: pic2.png] In Kenya, sand holds water.\n\n"
                      "# OUTRO\n[visual: pic3.png] Subscribe for more.\n", encoding="utf-8")
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=30", tmp_path / "music.wav")
    monkeypatch.setattr(outputs, "EXPORTS", tmp_path / "Exports")
    folder = tmp_path / "Exports" / "smart_village_boy" / "Story"
    (folder / "Upload").mkdir(parents=True)
    (folder / "Upload" / "Story_Short_Part2.mp4").write_bytes(b"old")

    # offline stand-ins: a tone per scene for the voice, evenly spread captions, no face model, no Kaggle
    monkeypatch.setattr(mv, "FOOTAGE_DIRS", [])
    monkeypatch.setattr(mv, "tts_edge", lambda text, out, voice, rate: ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={0.4 * len(text.split()):.2f}", out))

    durations = {}
    real_narrate = mv.narrate

    def narrate(scenes, work, args):
        path = real_narrate(scenes, work, args)
        durations.update(enumerate(s["dur"] for s in scenes))
        return path
    monkeypatch.setattr(mv, "narrate", narrate)

    def captions_from_durations(audio, out, text, model, words):
        chunks, t = [], 0.0
        for i, s in enumerate(mv.parse_script(script)):
            chunks.append(mv.spread(s["text"].split(), t, t + 0.4 * len(s["text"].split())))
            t += durations[i]
        return mv.write_srt(chunks, out), chunks
    monkeypatch.setattr(mv, "make_captions", captions_from_durations)
    monkeypatch.setattr(captions, "scene_faces", lambda scenes, fmt, **kw: [[] for _ in scenes])
    monkeypatch.setattr(upscale, "upscale_scenes", lambda *a, **k: None)
    monkeypatch.setattr(mv.sys, "argv", ["make_video.py", str(script), "--engine", "edge", "--no-generate-visuals",
                                         "--voice", "en-US-EmmaMultilingualNeural", "--depth", "off", "--music",
                                         str(tmp_path / "music.wav"), "--no-sound-effects"])
    mv.main()

    upload, working = folder / "Upload", folder / "Working"
    narration = sum(durations.values())
    intro_h, intro_v = 60, 90  # frames: 2 s, and the vertical clip's 3 s of sound
    for name, (w, h), intro in (("Story.mp4", (1920, 1080), intro_h), ("Story_Short.mp4", (1080, 1920), intro_v)):
        width, height, secs, audio = probe(upload / name)
        expected = rr.timeline(list(durations.values()), mv.FPS, intro, rr.OUTRO)[3] / mv.FPS
        assert (width, height, audio) == (w, h, True)
        assert secs == pytest.approx(expected, abs=0.1), name
        assert narration + intro / mv.FPS < secs
    # each output's captions start after its own intro
    lead = {"Story.srt": (intro_h - rr.TRANSITION // 2) / mv.FPS, "Story_Short.srt": (intro_v - rr.TRANSITION // 2) / mv.FPS}
    for name, first in lead.items():
        assert srt_starts(working / name)[0] == pytest.approx(first, abs=0.01), name
    # the intro clip's own tone plays at the start; the music only comes in after the intro
    assert loudness(upload / "Story.mp4", 0.2, 1.5) > -35
    # the manifest lists this render's files; the stale part moved out of Upload
    manifest = json.loads((working / "manifest.json").read_text(encoding="utf-8"))["files"]
    assert {"Story.mp4", "Story_Short.mp4", "thumbnail.jpg", "youtube.md"} <= set(manifest)
    assert sorted(p.name for p in upload.iterdir()) == sorted(manifest)
    assert [p.name for p in (working / "old_outputs").glob("*/*")] == ["Story_Short_Part2.mp4"]
    assert not (working / "render").exists()  # checkpoints cleaned up after the successful render
