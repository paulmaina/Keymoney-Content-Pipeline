"""Tests for the HyperFrames renderer backend (--renderer hyperframes). No Node, Chrome, or FFmpeg."""
from pathlib import Path
from types import SimpleNamespace

import pytest

import channels
import make_video as mv
import outputs
import shorts
from video_producer import hyperframes_render as hf


def test_renderer_accepts_hyperframes_and_branding_applies():
    args = mv.parse_args(["s.txt", "--renderer", "hyperframes"])
    assert args.renderer == "hyperframes"
    assert shorts.branding_of(args) is not None
    assert hf.lead_seconds(args, "horizontal") == pytest.approx(2.0)


def test_scene_spans_math():
    lead, spans, total = hf.scene_spans([4.0, 5.5], intro_sec=2.0, outro_sec=3.5)
    assert lead == pytest.approx(2.0)
    assert spans == [(2.0, 4.0), (6.0, 5.5)]
    assert total == pytest.approx(15.0)


def test_write_project_follows_hyperframes_contract(tmp_path):
    from PIL import Image
    font = tmp_path / "font.ttf"
    font.write_bytes(b"ttf")
    pic = tmp_path / "s0.jpg"
    Image.new("RGB", (800, 1200), (40, 80, 120)).save(pic)

    profile = channels.load("smart_village_boy")
    args = SimpleNamespace(profile=profile, channel=profile.name, camera="bold", caption_style="auto", no_branding=False)
    scenes = [
        {"text": "First scene in Kenya.", "video": True},
        {"text": "Second fallback card scene.", "video": False},
    ]
    assets_list = [
        {"kind": "image", "src": "s0.jpg", "shots": [{"src": "s0.jpg"}]},
        {"kind": "card"},
    ]
    caption_lines = [
        {"start": 65, "end": 110, "y": 0.64, "words": [{"text": "First", "key": False}, {"text": "Kenya.", "key": True}]}
    ]
    places_list = [{"frame": 70, "label": "Kitui, Kenya", "showMap": True}]

    proj = hf.write_project(
        tmp_path / "proj",
        name="demo_short",
        fmt="vertical",
        scenes=scenes,
        assets_list=assets_list,
        moves=[["zoom-in"], ["pan-left"]],
        spans=[(2.0, 4.0), (6.0, 3.5)],
        total_sec=13.0,
        caption_lines_list=caption_lines,
        places_list=places_list,
        font_path=font,
        args=args,
        intro_sec=2.0,
        outro_sec=3.5,
        part=(1, 3),
    )

    index_html = (proj / "index.html").read_text(encoding="utf-8")
    captions_html = (proj / "compositions" / "captions.html").read_text(encoding="utf-8")
    overlays_html = (proj / "compositions" / "overlays.html").read_text(encoding="utf-8")

    assert 'data-composition-id="main"' in index_html
    assert 'data-width="1080" data-height="1920"' in index_html
    assert 'window.__timelines["main"] = tl;' in index_html
    assert 'data-composition-src="compositions/captions.html"' in index_html
    assert 'data-composition-src="compositions/overlays.html"' in index_html

    assert captions_html.startswith("<template>")
    assert 'window.__timelines["captions"] = tl;' in captions_html
    assert '<span class="em">Kenya.</span>' in captions_html

    assert overlays_html.startswith("<template>")
    assert 'window.__timelines["overlays"] = tl;' in overlays_html
    assert "PART 1 OF 3" in overlays_html
    assert "Watch Part 2" in overlays_html
    assert "Kitui, Kenya" in overlays_html


def test_render_all_hyperframes_invokes_cli_and_muxes(tmp_path, monkeypatch):
    from PIL import Image
    import captions
    import remotion_render as rr

    pic = tmp_path / "p1.png"
    Image.new("RGB", (640, 360), (80, 140, 200)).save(pic)
    script = tmp_path / "Story.txt"
    script.write_text("# Story\n# channel: smart_village_boy\n\n# HOOK\nHello Kenya.\n", encoding="utf-8")
    font = tmp_path / "font.ttf"
    font.write_bytes(b"ttf")
    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"wav")

    out = outputs.Outputs(tmp_path / "Exports" / "Story").make()
    args = mv.parse_args([str(script), "--renderer", "hyperframes", "--format", "horizontal", "--no-sound-effects"])
    scenes = [{"text": "Hello Kenya.", "dur": 2.5, "src": pic, "video": False, "description": ""}]
    chunks = [[mv.Word("Hello", 0.0, 0.5), mv.Word("Kenya.", 0.5, 1.0)]]

    monkeypatch.setattr(hf, "available", lambda: (True, None))
    monkeypatch.setattr(captions, "scene_faces", lambda *a, **k: [[]])
    monkeypatch.setattr(rr, "timeline_audio", lambda *a, **k: tmp_path / "timeline.wav")

    hf_calls = []

    def fake_run_hf(*cli_args, cwd, capture=False):
        hf_calls.append((cli_args, cwd))
        out_idx = cli_args.index("--output") + 1
        Path(cli_args[out_idx]).write_bytes(b"silent_mp4")
        return ""

    def fake_mux(silent, audio, args, dest, **kw):
        dest.write_bytes(b"final_mp4")
        return dest

    monkeypatch.setattr(hf, "run_hf", fake_run_hf)
    monkeypatch.setattr(mv, "mux", fake_mux)

    outs, lead_in, total = hf.render_all(scenes, tmp_path / "work", narration, chunks, font, args, ["horizontal"], out, script)
    assert len(outs) == 1 and outs[0].read_bytes() == b"final_mp4"
    assert lead_in == pytest.approx(2.0)
    assert total == pytest.approx(8.0)
    assert hf_calls and hf_calls[0][0][0] == "render"
