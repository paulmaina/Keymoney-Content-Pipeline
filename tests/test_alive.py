"""Still pictures that feel alive: depth plates, overlays, bold camera moves and chapter transitions, CC0 ambience,
second shots and --preview-scenes. No network, Node, GPU or Cloudflare: everything is faked or computed locally."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

import ai_visuals as av
import make_video as mv
import overlays
import remotion_render as rr
import script_check
import sfx
from test_ai_visuals import CREDS, FakeAPI, scenes


# ---------- overlays ----------

@pytest.mark.parametrize("description,prompt,expected", [
    ("Two farmers stretching a mesh net in the early morning fog", "", ["mist"]),
    ("A woman vendor at a market in harsh midday sunlight", "", ["heat"]),
    ("Villagers walking across a root bridge in a misty rainforest", "", ["mist", "leaves"]),
    ("Heavy rain falling on a dusty road at midday", "", ["rain"]),  # rain rules out dust and heat
    ("A family around a cooking fire at night", "", ["fire", "smoke"]),
    ("Close view of hands holding a clay pot", "sunbeams through a window", ["rays"]),
    ("A man reading indoors", "", []),
])
def test_overlays_come_from_the_visuals_line_and_prompt(description, prompt, expected):
    assert overlays.choose({"description": description, "image_prompt": prompt}) == expected


def test_overlays_ignore_the_narration_and_stop_at_two():
    assert overlays.choose({"text": "It rained for weeks.", "description": "A dry village street"}) == []
    many = "fog over a river in a forest with sunbeams and dust"
    assert len(overlays.choose({"description": many})) == overlays.MAX_PER_SCENE


# ---------- camera ----------

def test_bold_moves_never_repeat_back_to_back_even_between_two_shots():
    counts = [2, 1, 2, 2, 1, 2, 1, 1, 2, 2] * 3
    plan = rr.plan_shot_moves(counts, "seed", camera="bold")
    flat = [m for names in plan for m in names]
    assert [len(n) for n in plan] == counts and all(a != b for a, b in zip(flat, flat[1:]))
    assert set(flat) <= set(rr.BOLD_MOVES) and len(set(flat)) >= 6  # varied


def test_pictures_with_depth_get_moves_that_travel_through_the_scene():
    plan = rr.plan_shot_moves([2, 2], "s", masked={(0, 0), (1, 1)}, camera="bold")
    assert plan[0][0] in rr.BOLD_PARALLAX and plan[1][1] in rr.BOLD_PARALLAX


def test_bold_moves_are_bigger_but_never_show_an_edge():
    calm = rr.move_props("pan-left", False, 16 / 9, 1920, 1080, "calm")
    bold = rr.move_props("pan-left", False, 16 / 9, 1920, 1080, "bold")
    assert abs(bold["from"]["x"]) > abs(calm["from"]["x"])
    for p in (bold["from"], bold["to"]):
        assert abs(p["x"]) <= rr.safe_offset(p["scale"], 16 / 9, 1920, 1080, "x") + 1e-6
    strong = rr.move_props("zoom-in", True, 16 / 9, 1920, 1080, "bold")
    assert strong["to"]["scale"] == pytest.approx(1 + 0.2 * 1.35)


def test_chapter_transitions_alternate_whip_and_zoom_with_a_bold_camera():
    sections = ["HOOK", "HOOK", "HACK 1 KENYA", "HACK 1", "HACK 2 IRAN", "HACK 3", None]
    assert rr.plan_transitions(sections, "bold") == ["fade", "fade", "whip", "fade", "zoom", "whip", "fade"]
    assert set(rr.plan_transitions(sections, "calm")) == {"fade"}


# ---------- props ----------

def test_props_carry_shots_overlays_parallax_and_transition():
    args = SimpleNamespace(no_branding=False, channel="C", caption_style="auto", camera="bold", depth="strong",
                           overlays=True)
    shot = {"src": "a.jpg", "width": 1536, "height": 864, "mask": "a_fg.png", "plate": "a_bg.jpg"}
    asset = {"kind": "image", **shot, "shots": [shot, {"src": "b.jpg", "width": 1536, "height": 864}]}
    scene = {"text": "x", "video": False, "description": "early morning fog over hills"}
    props = rr.build_props("horizontal", [scene], [asset], [["zoom-in", "pan-left"]], [300], [0], 400, [], [], args,
                           transitions=["whip"])["scenes"][0]
    assert [s["from"] for s in props["shots"]] == [0, 150] and props["shots"][0]["plate"] == "a_bg.jpg"
    assert props["shots"][1]["move"]["name"] == "pan-left" and props["ease"] == rr.EASE["bold"]
    assert props["parallax"] == rr.DEPTH["strong"] and props["overlays"] == ["mist"] and props["transition"] == "whip"
    off = rr.build_props("horizontal", [scene], [asset], [["zoom-in"]], [300], [0], 400, [], [],
                         SimpleNamespace(**{**vars(args), "depth": "off", "overlays": False}))["scenes"][0]
    assert off["parallax"] is None and off["overlays"] == []


def test_stock_clips_and_cards_get_no_shots_or_overlays():
    args = SimpleNamespace(no_branding=False, channel="C", caption_style="auto", camera="bold", depth="strong",
                           overlays=True)
    props = rr.build_props("horizontal", [{"text": "x"}, {"text": "y"}], [{"kind": "video", "src": "c.mp4"},
                           {"kind": "card"}], ["zoom-in", "zoom-out"], [100, 100], [0, 84], 300, [], [], args)
    assert all("shots" not in s and "overlays" not in s for s in props["scenes"])


# ---------- depth ----------

def test_background_plate_fills_in_the_subject_and_keeps_the_rest(tmp_path):
    import depth
    img = np.zeros((216, 384, 3), np.uint8)
    img[:, :] = (40, 120, 200)  # sky-blue backdrop
    img[60:200, 150:230] = (20, 20, 20)  # a dark subject in front
    Image.fromarray(img[..., ::-1]).save(tmp_path / "pic.png")
    mask = np.zeros((216, 384), np.uint8)
    mask[60:200, 150:230] = 255
    Image.fromarray(mask).save(tmp_path / "pic_fg.png")
    plate = np.asarray(Image.open(depth.background_plate(tmp_path / "pic.png", tmp_path / "pic_fg.png")))
    assert plate[130, 190].mean() > 80  # where the subject was is now backdrop-coloured, not its dark copy
    original = np.asarray(Image.open(tmp_path / "pic.png").convert("RGB"))
    assert np.abs(plate[10, 10].astype(int) - original[10, 10]).max() < 12  # far from the subject: unchanged


# ---------- ambience ----------

def test_every_ambience_loop_is_cc0_with_its_source_recorded():
    src = sfx.sources()
    assert set(src) == {"wind", "water", "birds", "fire", "market", "rain"}
    for cat, info in src.items():
        assert info["licence"] == "CC0" and sfx.loop_file(cat) and info["source"]
        assert info["page"] is None if cat == "fire" else info["page"].startswith("https://commons.wikimedia.org/")


@pytest.mark.parametrize("text,expected", [
    ("Heavy rain over the village market", "rain"),
    ("A busy open-air market in northern Nigeria", "market"),
    ("Water dripping from a fog net into a gutter", "water"),
    ("A boy on a Kenyan hilltop at sunrise", "birds"),
    ("Sea fog rolling over dry coastal hills", "wind"),
    ("A man reading in a small room", None),
])
def test_ambience_matches_the_scene(text, expected):
    assert sfx.choose({"description": text}) == expected


def test_beds_merge_neighbouring_scenes_with_the_same_sound():
    beds = sfx.beds(["birds", "birds", None, "water", "birds"], [(0, 5), (5, 9), (9, 12), (12, 15), (15, 20)])
    assert beds == [(0, 9, "birds"), (12, 15, "water"), (15, 20, "birds")]


def test_ambience_track_places_each_bed_and_the_record_keeps_licences(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mv, "run", lambda cmd, cwd=None: calls.append([str(c) for c in cmd]))
    assert sfx.ambience_track([], 10, tmp_path / "a.wav", 0.5) is None
    sfx.ambience_track([(0, 9, "birds"), (12, 15, "water")], 20, tmp_path / "a.wav", 0.5)
    graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "adelay=12000|12000" in graph and "atrim=0:20.000" in graph and graph.count("volume=0.5") == 2
    rows = json.loads(sfx.record([{"number": 6}, {"number": 8}], ["birds", None], tmp_path / "s.json")
                      .read_text(encoding="utf-8"))["scenes"]
    assert rows[0]["scene"] == 6 and rows[0]["licence"] == "CC0" and rows[0]["page"] and rows[1] == {"scene": 8, "sound": None}


def test_music_and_ambience_are_both_ducked_under_the_voice():
    g = mv.background_filter(30.0, 0.2, 3)
    assert "[2:a]" in g and "[3:a]" in g and "amix=inputs=2:duration=first:normalize=0[bg]" in g
    assert "[bg][key]sidechaincompress" in g
    only = mv.background_filter(30.0, None, 2)
    assert "[2:a]" in only and "[m]" not in only and "sidechaincompress" in only


# ---------- second shots ----------

class SecondShotAPI(FakeAPI):
    def __call__(self, model, payload, creds, timeout=120):
        if model == av.TEXT_MODEL and payload["messages"][0]["content"] == av.SECOND_SYSTEM:
            self.calls.append((model, payload))
            return {"response": "Close-up of the same weathered hands lowering the small clay pot into the larger one."}
        return super().__call__(model, payload, creds, timeout)


@pytest.fixture
def api2(monkeypatch, tmp_path):
    fake = SecondShotAPI()
    monkeypatch.setattr(av, "cf_run", fake)
    monkeypatch.setattr(av, "GEN_DIR", tmp_path / "generated")
    monkeypatch.setattr(av.time, "sleep", lambda s: None)
    return fake


def test_long_scenes_get_a_second_shot_that_is_saved_and_reused(api2, tmp_path):
    prompts = tmp_path / "p.json"
    sc = scenes("A teacher sets one clay pot inside another.", "Short.")
    av.generate_scene_visuals(sc, prompts, creds=CREDS, formats=("horizontal",), second_shots={0})
    assert sc[0]["srcs2"]["horizontal"].exists() and "srcs2" not in sc[1]
    assert sc[0]["srcs2"]["horizontal"] != sc[0]["srcs"]["horizontal"]
    rec = json.loads(prompts.read_text(encoding="utf-8"))["scenes"][0]
    assert rec["prompt2"].startswith("Close-up of the same") and rec["prompt2_for"] == rec["prompt"]
    calls = len(api2.calls)
    again = scenes("A teacher sets one clay pot inside another.", "Short.")
    av.generate_scene_visuals(again, prompts, creds=CREDS, formats=("horizontal",), second_shots={0})
    assert len(api2.calls) == calls and again[0]["srcs2"] == sc[0]["srcs2"]  # nothing new: prompts and images cached


def test_second_shot_without_the_text_model_flips_the_distance():
    assert av.fallback_second("Medium shot of a farmer").startswith("Close-up detail")
    assert av.fallback_second("Close-up of hands").startswith("Wide establishing shot")


def test_two_shot_scenes_are_the_long_ones_and_can_be_turned_off():
    sc = [{"text": " ".join(["word"] * n)} for n in (8, 30)]
    args = SimpleNamespace(engine="edge", rate="+5%", two_shots=True)
    assert mv.two_shot_scenes(sc, args) == {1}
    assert mv.two_shot_scenes(sc, SimpleNamespace(**{**vars(args), "two_shots": False})) == set()


def test_stand_in_prompts_are_written_properly_next_time():
    rec = {"narration": "x", "prompt": "Scene illustrating: x", "stand_in": True}
    key = av.scene_id("x")
    assert av.saved_prompt({key: rec}, {"text": "x", "id": key}) is None
    assert av.saved_prompt({key: {**rec, "stand_in": False}}, {"text": "x", "id": key}) == "Scene illustrating: x"


def test_check_shows_the_second_shots_neurons(tmp_path, capsys):
    p = tmp_path / "s.txt"
    p.write_text("\n\n".join(["# Visuals: a dusty road\n" + "Farmers dig small pits and fill them with compost. "
                              "Rain soaks deep into the ground and stays."] * 3), encoding="utf-8")
    on = script_check.report(p, mv.parse_args([str(p), "-o", str(tmp_path / "o"), "--generate-visuals"]))
    out = capsys.readouterr().out
    est = on["estimates"]
    assert est["second_scenes"] == 3 and est["second_images_new"] == 6 and est["neurons_second"] > 6 * 130
    assert "second shots:" in out and "--no-two-shots" in out
    off = script_check.analyse(p, mv.parse_args([str(p), "-o", str(tmp_path / "o"), "--generate-visuals",
                                                 "--no-two-shots"]))["estimates"]
    assert off["second_scenes"] == 0 and off["neurons_total"] < est["neurons_total"] - est["neurons_second"] + 1


def test_second_shots_are_upscaled_and_their_faces_kept_clear(tmp_path, monkeypatch):
    import captions
    import upscale
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    for f in (a, b):
        Image.new("RGB", (32, 18)).save(f)
    s = {"srcs": {"horizontal": a}, "srcs2": {"horizontal": b}, "src": a, "src2": b}
    assert upscale.scene_images([s], ["horizontal"]) == [a, b]
    monkeypatch.setattr(captions, "frame_faces", lambda src, fmt, **kw: [(0.1, 0.2)] if src == a else [(0.5, 0.6)])
    assert captions.scene_faces([s], "horizontal") == [[(0.1, 0.2), (0.5, 0.6)]]


# ---------- --preview-scenes ----------

def test_preview_options_and_annotations(tmp_path):
    a = mv.parse_args(["s.txt", "--preview-scenes", "1,6,8,14"])
    assert a.preview == {0, 5, 7, 13}
    p = tmp_path / "s.txt"
    p.write_text("# HOOK - Visuals: a hill\nNo fridge.\n\n# HACK 1 - Visuals: a road\n# location: Kitui, Kenya\nHere.\n\n"
                 "# Visuals: a lake\nIn Iran, water.\n", encoding="utf-8")
    sc = mv.parse_script(p)
    mv.annotate(sc, p)
    assert [(s["number"], s["section"]) for s in sc] == [(1, "HOOK"), (2, "HACK 1"), (3, "HACK 1")]
    import places
    picked = [sc[1], sc[2]]  # any selection keeps its own # location line
    assert [pl.label for _, pl, _, _ in places.scene_places(picked, p)] == ["Kitui, Kenya", "Iran"]


def test_preview_keeps_the_other_scenes_saved_prompts(api2, tmp_path):
    prompts = tmp_path / "p.json"
    prompts.write_text(json.dumps({"scenes": [{"scene": 1, "narration": "One.", "prompt": "first"},
                                              {"scene": 2, "narration": "Two.", "prompt": "second"}]}), encoding="utf-8")
    av.generate_scene_visuals(scenes("One.", "Two.", "Three."), prompts, creds=CREDS, formats=("horizontal",), only={2})
    saved = json.loads(prompts.read_text(encoding="utf-8"))["scenes"]
    assert [(r["narration"], r["prompt"]) for r in saved][:2] == [("One.", "first"), ("Two.", "second")]
    assert saved[2]["narration"] == "Three."


def test_preview_renders_only_the_picked_scenes_to_working_preview(tmp_path, monkeypatch):
    import outputs
    p = tmp_path / "Story.txt"
    p.write_text("\n\n".join(f"# Visuals: scene {n}\nScene number {n} is here." for n in range(1, 6)), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(mv, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(rr, "available", lambda: (True, ""))
    monkeypatch.setattr(mv, "narrate", lambda sc, work, args: seen.setdefault("texts", [s["text"] for s in sc]) and work)
    monkeypatch.setattr(mv, "make_captions", lambda *a, **k: (None, None))
    monkeypatch.setattr(mv, "duration", lambda path: 12.0)
    monkeypatch.setattr(mv, "stamp_draft", lambda video, font: seen.setdefault("label", video))

    def render_all(sc, work, narration, chunks, font, args, formats, out, script, parts, checkpoints=None):
        seen.update(formats=formats, video=out.video(formats[0]), parts=parts)
        return [], 0.0, 0.0
    monkeypatch.setattr(rr, "render_all", render_all)
    monkeypatch.setattr(outputs, "EXPORTS", tmp_path / "Exports")
    monkeypatch.setattr(mv.sys, "argv", ["make_video.py", str(p), "--preview-scenes", "2,4", "--no-generate-visuals",
                                         "--engine", "edge"])
    mv.main()
    assert seen["texts"] == ["Scene number 2 is here.", "Scene number 4 is here."]
    assert seen["formats"] == ["horizontal"] and seen["parts"] == []
    assert seen["video"] == seen["label"] == tmp_path / "Exports" / "smart_village_boy" / "Story" / "Working" / "preview.mp4"


def test_a_rainforest_is_not_rain():
    s = {"description": "Khasi villagers on a riverbank in a misty Meghalaya rainforest"}
    assert "rain" not in overlays.choose(s) and sfx.choose(s) != "rain"
