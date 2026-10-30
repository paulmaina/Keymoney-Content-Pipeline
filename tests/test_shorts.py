"""The vertical video as a numbered series of Shorts: splitting, rendering each part, --check and the YouTube notes.
No Node, ffmpeg or network: Remotion and ffmpeg are faked."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import captions
import make_video as mv
import remotion_render as rr
import script_check
import shorts
import youtube_meta as ym

INTRO_OUTRO = (rr.INTRO + rr.OUTRO - rr.TRANSITION) / mv.FPS


def script(tmp_path, scenes, header=""):
    """scenes: [(heading or None, narration, extra '#' lines)]"""
    blocks = [header] if header else []
    for heading, text, *extra in scenes:
        lines = ([f"# {heading} - Visuals: a dusty road"] if heading else ["# Visuals: a dusty road"]) + list(extra)
        blocks.append("\n".join(lines + [text]))
    p = tmp_path / "s.txt"
    p.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return p


# ---------- splitting ----------

def test_overhead_is_the_intro_and_outro_minus_their_crossfades():
    assert shorts.part_seconds([10, 20]) == pytest.approx(30 + INTRO_OUTRO)
    assert shorts.part_seconds([10], branding=None) == 10  # the ffmpeg renderer has no intro or outro


def test_everything_in_one_part_when_it_fits():
    assert shorts.split([20] * 7, branding=True) == [(0, 7)]


def test_parts_stay_under_the_limit_and_cover_every_scene():
    durs = [11.5] * 60
    parts = shorts.split(durs)
    assert parts[0][0] == 0 and parts[-1][1] == 60
    assert all(a == prev_b for (_, prev_b), (a, _) in zip(parts, parts[1:]))
    assert all(shorts.part_seconds(durs[a:b]) <= shorts.LIMIT for a, b in parts)
    assert len(parts) == 5  # 60 x 11.5 s = 690 s needs 5 parts of under 170 s


def test_cuts_go_at_chapter_headings_even_if_that_takes_one_more_part():
    # 4 chapters of 100 s: two would fit in 2 parts only by cutting inside a chapter
    durs = [10.0] * 40
    sections = [f"HACK {i // 10 + 1}" for i in range(40)]
    parts = shorts.split(durs, sections)
    assert parts == [(0, 10), (10, 20), (20, 30), (30, 40)]
    # short chapters are grouped, still cut only at headings
    small = [f"HACK {i // 4 + 1}" for i in range(24)]  # 6 chapters of 40 s
    parts = shorts.split([10.0] * 24, small)
    assert all(a % 4 == 0 for a, _ in parts) and len(parts) == 2


def test_a_chapter_longer_than_a_short_is_cut_inside_it_evenly():
    durs = [10.0] * 30  # one 300 s chapter
    parts = shorts.split(durs, ["HACK 1"] * 30)
    assert len(parts) == 2 and abs((parts[0][1] - parts[0][0]) - (parts[1][1] - parts[1][0])) <= 1


def test_hack_1_and_hack_1_kenya_are_one_chapter():
    assert shorts.chapter_starts(["HOOK", "HACK 1 KENYA", "HACK 1", "HACK 2 IRAN"]) == {1, 3}


def test_a_scene_over_the_limit_is_a_part_of_its_own_and_flagged():
    parts = shorts.split([30, 200, 30])
    assert (1, 2) in parts
    lines = shorts.describe(parts, [30, 200, 30], [None] * 3, True)
    assert any("over the limit" in l for l in lines)


def test_describe_lists_scene_ranges_lengths_and_chapters():
    lines = shorts.describe([(0, 2), (2, 3)], [60, 50, 40], ["HOOK", "HACK 1 KENYA", "HACK 2"], True)
    assert lines[0].startswith("Part 1: scenes 1-2, 1:5") and "(Hook, Hack 1: Kenya)" in lines[0]
    assert lines[1].startswith("Part 2: scene 3, 0:") and "(Hack 2)" in lines[1]


# ---------- rendering ----------

def test_cuts_full_horizontal_and_numbered_vertical_parts(tmp_path):
    base = tmp_path / "vid"
    scenes = [{"text": "x"}] * 6
    cuts = rr.plan_cuts(scenes, ["horizontal", "vertical"], base, [(0, 3), (3, 6)])
    assert [(f, a, b, p, o.name) for f, a, b, p, o in cuts] == [
        ("horizontal", 0, 6, None, "vid_horizontal.mp4"),
        ("vertical", 0, 3, (1, 2), "vid_vertical_part1.mp4"), ("vertical", 3, 6, (2, 2), "vid_vertical_part2.mp4")]
    one = rr.plan_cuts(scenes, ["vertical"], base, [(0, 6)])
    assert [(o.name, p) for *_, p, o in one] == [("vid_vertical.mp4", None)]  # one part: a plain vertical video


def test_shift_chunks_keeps_a_part_s_words_retimed():
    W = mv.Word
    chunks = [[W("a", 0.0, 0.4), W("b", 4.9, 5.2)], [W("c", 5.5, 6.0), W("d", 9.0, 9.5)]]
    assert rr.shift_chunks(chunks, 5.0, 9.0) == [[W("c", 0.5, 1.0)]]
    assert rr.shift_chunks(chunks, 0.0, 5.0) == [[W("a", 0.0, 0.4), W("b", 4.9, 5.2)]]


def test_props_carry_the_part(tmp_path):
    args = SimpleNamespace(no_branding=False, channel="C", caption_style="auto")
    props = rr.build_props("vertical", [], [], [], [], [], 100, [], [], args, part=(2, 3))
    assert props["part"] == {"index": 2, "count": 3}
    assert rr.build_props("horizontal", [], [], [], [], [], 100, [], [], args)["part"] is None


def test_render_all_renders_each_part_from_its_own_scenes_audio_and_captions(tmp_path, monkeypatch):
    p = script(tmp_path, [("HOOK", "No fridge here."), ("HACK 1 KENYA", "In Kenya, sand holds water.", "# location: Kitui, Kenya"),
                          ("HACK 2 IRAN", "In Iran, tunnels carry water."), (None, "Many still flow today.")])
    scenes = mv.parse_script(p)
    for s, d in zip(scenes, [10.0, 12.0, 11.0, 9.0]):
        s["dur"] = d
    W = mv.Word
    chunks = [[W("No", 0.1, 0.4), W("fridge", 0.5, 0.9)], [W("In", 10.2, 10.4), W("Kenya,", 10.5, 11.0)],
              [W("In", 22.3, 22.5), W("Iran,", 22.6, 23.0)], [W("Many", 33.2, 33.6)]]
    calls, props = [], {}
    monkeypatch.setattr(rr, "ensure_installed", lambda: None)
    monkeypatch.setattr(rr, "prepare_assets", lambda sc, *a, **k: [{"kind": "card"} for _ in sc])
    monkeypatch.setattr(rr, "save_speed", lambda *a: None)
    monkeypatch.setattr(captions, "scene_faces",
                        lambda sc, fmt, **kw: [[] for _ in sc])

    def npx(*a, **k):
        if a[:2] == ("remotion", "render"):
            prop = Path(next(x for x in a if str(x).startswith("--props=")).split("=", 1)[1])
            props[Path(a[4]).name] = json.loads(prop.read_text(encoding="utf-8"))
    monkeypatch.setattr(rr, "npx", npx)
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: calls.append([str(c) for c in cmd]) or "")
    monkeypatch.setattr(mv, "mux", lambda video, audio, args, out, **k: out)
    args = SimpleNamespace(no_branding=False, channel="C", caption_style="auto", music=None, no_parallax=True)
    font = tmp_path / "f.ttf"
    font.write_bytes(b"x")
    outs, lead, total = rr.render_all(scenes, tmp_path / "work", tmp_path / "narration.wav", chunks, font, args,
                                      ["horizontal", "vertical"], tmp_path / "vid", p, parts=[(0, 2), (2, 4)])
    assert [o.name for o in outs] == ["vid_horizontal.mp4", "vid_vertical_part1.mp4", "vid_vertical_part2.mp4"]
    full, one, two = props["horizontal_silent.mp4"], props["vertical_part1_silent.mp4"], props["vertical_part2_silent.mp4"]
    assert len(full["scenes"]) == 4 and full["part"] is None
    assert (len(one["scenes"]), one["part"]) == (2, {"index": 1, "count": 2})
    assert (len(two["scenes"]), two["part"]) == (2, {"index": 2, "count": 2})
    # part 2's captions start with its own first words, right after its intro
    first = two["captions"][0]["words"][0]
    assert first["text"] == "In" and first["start"] == rr.INTRO - rr.TRANSITION // 2 + round(0.3 * mv.FPS)
    assert [p["label"] for p in one["places"]] == ["Kitui, Kenya"]  # the part's own # location line
    assert [p["label"] for p in two["places"]] == ["Iran"] and two["places"][0]["showMap"]  # its own map pin
    cuts = [c for c in calls if "-ss" in c and c[c.index("-i") + 1].endswith("narration.wav")]
    assert [(c[c.index("-ss") + 1], c[c.index("-t") + 1]) for c in cuts] == [("0.000", "22.000"), ("22.000", "20.000")]
    assert total == pytest.approx((sum([10, 12, 11, 9]) * mv.FPS + rr.INTRO + rr.OUTRO - rr.TRANSITION) / mv.FPS, abs=0.05)


def test_ffmpeg_renderer_cuts_the_vertical_video_into_parts(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: calls.append([str(c) for c in cmd]) or "")
    video = tmp_path / "vid_vertical.mp4"
    video.write_bytes(b"x")
    scenes = [{"dur": d} for d in (10.0, 20.0, 30.0)]
    outs = mv.cut_parts(video, scenes, [(0, 2), (2, 3)], tmp_path / "vid")
    assert [o.name for o in outs] == ["vid_vertical_part1.mp4", "vid_vertical_part2.mp4"] and not video.exists()
    assert [(c[c.index("-ss") + 1], c[c.index("-t") + 1]) for c in calls] == [("0.000", "30.000"), ("30.000", "30.000")]


def test_visuals_only_turns_on_visuals():
    a = mv.parse_args(["s.txt", "--visuals-only", "--no-generate-visuals"])
    assert a.visuals_only and a.generate_visuals


# ---------- --check ----------

LONG = [("HOOK", "No fridge. No power. No running water. Yet villages keep food cold and water close.")] + [
    (f"HACK {i // 10 + 1}" if i % 10 == 0 else None,
     "Farmers dig small pits and fill them with compost. Rain soaks deep into the ground and stays there.")
    for i in range(60)]


def test_check_reports_the_main_video_and_each_part(tmp_path, capsys):
    p = script(tmp_path, LONG)
    args = mv.parse_args([str(p), "-o", str(tmp_path / "o"), "--generate-visuals", "--engine", "edge"])
    a = script_check.report(p, args)
    out = capsys.readouterr().out
    assert "Main video (horizontal): about" in out and f"a series of {len(a['parts'])} Shorts" in out
    assert len(a["parts"]) >= 3 and "Part 1: scenes 1-" in out
    assert all(shorts.part_seconds([r["seconds"] for r in a["rows"][x:y]]) <= shorts.LIMIT for x, y in a["parts"])
    assert all(x == 0 or (x - 1) % 10 == 0 for x, _ in a["parts"])  # every cut at a HACK heading


def test_check_warns_when_cloudflare_needs_several_days(tmp_path, capsys):
    p = script(tmp_path, LONG)
    args = mv.parse_args([str(p), "-o", str(tmp_path / "o"), "--generate-visuals", "--check-images"])
    a = script_check.report(p, args)
    out = capsys.readouterr().out
    days = a["estimates"]["days"]
    assert days == -(-int(a["estimates"]["neurons_total"]) // 10_000) and days >= 2
    assert f"needs about {days} days" in out and "--visuals-only" in out
    short = script(tmp_path, LONG[:10])
    script_check.report(short, mv.parse_args([str(short), "-o", str(tmp_path / "o2"), "--generate-visuals"]))
    assert "days of Cloudflare" not in capsys.readouterr().out


def test_video_tag_range_scales_with_length():
    assert script_check.video_range(16) == (1, 5) and script_check.video_range(60) == (5, 10)


# ---------- YouTube notes ----------

def test_each_part_gets_titles_description_and_tags_linking_the_full_video(tmp_path):
    p = script(tmp_path, LONG[:21], header="# Village Water Hacks That Beat Drought")
    scenes = mv.parse_script(p)
    for s in scenes:
        s["dur"] = 12.0
    parts = shorts.split([12.0] * len(scenes), mv.parse_sections(p))
    assert len(parts) == 2
    metas = [ym.part_meta(scenes, p, mv.parse_sections(p), parts, k, "Smart Village Boy") for k in (1, 2)]
    for m in metas:
        assert m["titles"] and all(t.endswith(f"(Part {m['k']}/2)") and len(t) <= ym.SHORT_TITLE_MAX for t in m["titles"])
        assert ym.FULL_LINK in m["description"] and "#Shorts" in m["description"] and "Chapters" not in m["description"]
        assert m["tags"]
    assert "Next: Part 2." in metas[0]["description"] and "Next: Part" not in metas[1]["description"]
    md = ym.shorts_markdown(tmp_path / "vid", metas, "Village Water Hacks")
    assert "## Shorts series: 2 parts" in md and "vid_vertical_part2.mp4" in md and "Related video" in md


# ---------- review fixes: frame timeline, per-part subtitles, voice checks ----------

def test_frame_edges_never_drift_from_the_narration():
    durs = [1.01] * 30  # each scene alone would round to 30 frames (1.000 s): 0.3 s of drift over 30 scenes
    edges = mv.frame_edges(durs)
    assert edges[-1] == round(30.3 * mv.FPS) == 909
    assert all(abs(e / mv.FPS - 1.01 * i) <= 0.5 / mv.FPS + 1e-9 for i, e in enumerate(edges))  # within half a frame
    assert {b - a for a, b in zip(edges, edges[1:])} == {30, 31}


def test_ffmpeg_scenes_are_encoded_to_the_frames_the_cuts_use(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: calls.append([str(c) for c in cmd]) or "")
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    scenes = [{"text": "x", "dur": 1.01, "visual": None, "src": str(img), "srcs": {}} for _ in range(30)]
    monkeypatch.setattr(mv, "scene_src", lambda s, fmt: img)
    args = SimpleNamespace(music=None)
    monkeypatch.setattr(mv, "mux", lambda *a, **k: tmp_path / "v.mp4")
    (tmp_path / "w").mkdir()
    mv.render_format("vertical", scenes, tmp_path / "w", tmp_path / "n.wav", None, tmp_path / "f.ttf", args,
                     tmp_path / "v.mp4")
    frames = [int(c[c.index("-frames:v") + 1]) for c in calls if "-frames:v" in c]
    assert len(frames) == 30 and "-t" not in calls[0] and sum(frames[:15]) == mv.frame_edges([1.01] * 30)[15]
    calls.clear()
    video = tmp_path / "vid_vertical.mp4"
    video.write_bytes(b"x")
    mv.cut_parts(video, scenes, [(0, 15), (15, 30)], tmp_path / "vid")
    ss = float(calls[1][calls[1].index("-ss") + 1])
    assert ss == pytest.approx(sum(frames[:15]) / mv.FPS, abs=0.001)  # part 2 opens exactly on scene 16's first frame


def test_each_short_gets_its_own_retimed_srt(tmp_path):
    W = mv.Word
    scenes = [{"dur": d} for d in (10.0, 12.0, 11.0)]
    chunks = [[W("No", 0.1, 0.4), W("fridge.", 0.5, 0.9)], [W("In", 10.2, 10.4), W("Kenya,", 10.5, 11.0)],
              [W("In", 22.3, 22.5), W("Iran,", 22.6, 23.0)]]
    lead = (rr.INTRO - rr.TRANSITION // 2) / mv.FPS
    one, two = mv.write_part_srts(chunks, scenes, [(0, 2), (2, 3)], tmp_path / "vid", "remotion", lead)
    assert (one.name, two.name) == ("vid_vertical_part1.srt", "vid_vertical_part2.srt")
    text = two.read_text(encoding="utf-8")
    assert text.startswith(f"1\n{mv.srt_time(0.3 + lead)} --> {mv.srt_time(1.0 + lead)}\nIn Iran,")
    assert "Kenya" not in text and "fridge" not in text
    assert "No fridge." in one.read_text(encoding="utf-8") and "Iran" not in one.read_text(encoding="utf-8")
    # the ffmpeg renderer: no intro, and the part starts on its frame boundary
    flat = mv.write_part_srts(chunks, [{"dur": 1.01}] * 22 + [{"dur": 11.0}], [(0, 22), (22, 23)], tmp_path / "f",
                              "ffmpeg", 0.0)
    start = mv.frame_edges([1.01] * 22 + [11.0])[22] / mv.FPS
    assert flat[1].read_text(encoding="utf-8").startswith(f"1\n{mv.srt_time(22.3 - start)}")


def test_visuals_only_never_needs_the_voice_model(tmp_path, monkeypatch):
    import ai_visuals
    import stock
    p = script(tmp_path, [("HOOK", "No fridge here.")])
    drawn = []
    monkeypatch.setattr(mv, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(mv, "check_kokoro_model", lambda: pytest.fail("voice model checked before the images"))
    monkeypatch.setattr(rr, "available", lambda: (True, ""))
    monkeypatch.setattr(ai_visuals, "Providers", lambda *a, **k: None)
    monkeypatch.setattr(stock, "fill_stock", lambda *a, **k: {})
    monkeypatch.setattr(stock, "write_media_json", lambda *a, **k: None)
    monkeypatch.setattr(ai_visuals, "generate_scene_visuals", lambda *a, **k: drawn.append(1) or [])
    monkeypatch.setattr(mv.sys, "argv", ["make_video.py", str(p), "--visuals-only", "--engine", "kokoro",
                                         "-o", str(tmp_path / "out" / "v")])
    mv.main()
    assert drawn == [1]
