"""Tests for the Remotion renderer's planning (timing, moves, captions, places, parallax). No Node, network or ffmpeg."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import captions
import depth
import make_video as mv
import places
import remotion_render as rr

FPS = 30


# ---------- timeline ----------

def transition_series_length(intro, lengths, outro, t=rr.TRANSITION):
    """What Remotion's TransitionSeries makes of the sequences: each transition overlaps two neighbours."""
    seqs = ([intro] if intro else []) + lengths + ([outro] if outro else [])
    return sum(seqs) - t * (len(seqs) - 1)


@pytest.mark.parametrize("intro,outro", [(rr.INTRO, rr.OUTRO), (0, 0), (rr.INTRO, 0), (0, rr.OUTRO)])
def test_timeline_matches_transition_series_and_centres_fades_on_the_narration(intro, outro):
    durations = [8.2, 6.6, 7.43, 9.0]
    lead, lengths, bounds, total = rr.timeline(durations, FPS, intro, outro)
    assert transition_series_length(intro, lengths, outro) == total
    half = rr.TRANSITION // 2
    # scene i becomes visible half a transition before its words start, whatever came before
    starts, t = [], (intro - rr.TRANSITION if intro else 0)
    for length in lengths:
        starts.append(t)
        t += length - rr.TRANSITION
    assert [b - half for b in bounds] == starts
    assert bounds[0] == lead and lead == (intro - half if intro else half)
    narration_end = lead + round(sum(durations) * FPS)
    assert total == narration_end + (outro - half if outro else 0)


def test_timeline_never_drifts_on_long_videos():
    durations = [7.333] * 60  # 7.333 s doesn't divide into frames; rounding must not add up
    lead, lengths, bounds, total = rr.timeline(durations, FPS, rr.INTRO, rr.OUTRO)
    assert bounds[-1] == lead + round(7.333 * 59 * FPS)


# ---------- moves ----------

def test_moves_vary_and_never_repeat_back_to_back():
    names = rr.plan_moves([{}] * 40, "Script2")
    assert all(a != b for a, b in zip(names, names[1:]))
    assert len(set(names)) >= 6
    assert names == rr.plan_moves([{}] * 40, "Script2")  # same script, same film
    assert names != rr.plan_moves([{}] * 40, "Other")


def test_parallax_scenes_get_moves_that_go_in_or_out():
    names = rr.plan_moves([{}] * 10, "x", has_mask={2, 3, 7})
    assert {names[2], names[3], names[7]} <= set(rr.PARALLAX_MOVES) and names[2] != names[3]


@pytest.mark.parametrize("fmt", ["horizontal", "vertical"])
@pytest.mark.parametrize("name", list(rr.MOVES))
def test_moves_never_show_the_picture_edge(fmt, name):
    w, h = mv.FORMATS[fmt]["size"]
    for aspect in (1.0, 16 / 9, 9 / 16):
        for strong in (False, True):
            m = rr.move_props(name, strong, aspect, w, h)
            for p in (m["from"], m["to"]):
                assert p["scale"] >= 1
                assert abs(p["x"]) <= rr.safe_offset(p["scale"], aspect, w, h, "x") + 1e-6
                assert abs(p["y"]) <= rr.safe_offset(p["scale"], aspect, w, h, "y") + 1e-6


def test_strong_moves_travel_further():
    normal = rr.move_props("zoom-in", False, 1, 1920, 1080)
    strong = rr.move_props("zoom-in", True, 1, 1920, 1080)
    assert strong["to"]["scale"] - 1 > 1.5 * (normal["to"]["scale"] - 1) and "strong" in strong["name"]


def test_safe_offset_for_a_square_image_in_a_wide_frame():
    # a 1920x1920 cover in a 1920x1080 frame: no room sideways at scale 1, 420 px (38.9 %) up and down
    assert rr.safe_offset(1.0, 1.0, 1920, 1080, "x") == 0
    assert rr.safe_offset(1.0, 1.0, 1920, 1080, "y") == pytest.approx(420 / 1080 * 100)
    assert rr.safe_offset(1.1, 1.0, 1920, 1080, "x") == pytest.approx(5.0)


# ---------- props ----------

def test_caption_lines_shift_by_the_intro_and_hold_until_the_next_line():
    W = mv.Word
    chunks = [[W("Hello", 0.0, 0.4), W("world.", 0.4, 0.9)], [W("Next", 1.1, 1.5)], [W("End.", 5.0, 5.4)]]
    lines = rr.caption_lines(chunks, FPS, lead=52)
    assert lines[0]["start"] == 52 and lines[0]["end"] == 52 + round(1.1 * FPS)  # cut short by the next line
    assert lines[1]["end"] == 52 + round((1.5 + rr.CAPTION_HOLD) * FPS)  # held after its last word
    assert lines[0]["words"][1] == {"text": "world.", "start": 52 + 12, "end": 52 + 27}


def args(**kw):
    return SimpleNamespace(**{"no_branding": False, "channel": "Smart Village Boy", "no_parallax": False, **kw})


def test_build_props_marks_cards_and_strong_video_scenes():
    scenes = [{"text": "A", "video": True}, {"text": "Card text", "video": False}, {"text": "C", "video": False}]
    assets = [{"kind": "image", "src": "scene_000.jpg", "width": 1024, "height": 1024}, {"kind": "card"},
              {"kind": "image", "src": "scene_002.jpg", "width": 864, "height": 1536}]
    lead, lengths, bounds, total = rr.timeline([5, 5, 5], FPS, rr.INTRO, rr.OUTRO)
    p = rr.build_props("vertical", scenes, assets, ["zoom-in", "pan-left", "zoom-out"], lengths, bounds, total, [], [],
                       args())
    assert (p["width"], p["height"], p["durationInFrames"]) == (1080, 1920, total)
    assert "strong" in p["scenes"][0]["move"]["name"]  # a [video] scene shown as a still
    assert p["scenes"][1]["text"] == "Card text" and p["scenes"][0]["text"] is None
    assert "strong" not in p["scenes"][2]["move"]["name"]  # no [video] tag: a normal move
    assert p["introFrames"] == rr.INTRO
    assert rr.build_props("vertical", scenes, assets, ["zoom-in"] * 3, lengths, bounds, total, [], [],
                          args(no_branding=True))["outroFrames"] == 0


def picture(path, size):
    from PIL import Image
    Image.new("RGB", size, (90, 140, 60)).save(path)
    return path


def test_each_format_gets_its_own_image_and_shared_files_are_copied_once(tmp_path):
    wide, tall, square = (picture(tmp_path / n, s) for n, s in
                          (("w.jpg", (1536, 864)), ("t.jpg", (864, 1536)), ("s.jpg", (1024, 1024))))
    scenes = [{"src": wide, "srcs": {"horizontal": wide, "vertical": tall}}, {"src": square}, {"src": None}]
    public, state = tmp_path / "public", {}
    a = {f: rr.prepare_assets(scenes, public, args(no_parallax=True), f, state) for f in ("horizontal", "vertical")}
    assert (a["horizontal"][0]["width"], a["vertical"][0]["width"]) == (1536, 864)
    assert a["horizontal"][1]["src"] == a["vertical"][1]["src"]  # one square file serves both formats
    assert a["vertical"][2] == {"kind": "card"}
    assert sorted(p.name for p in public.iterdir()) == ["scene_000_horizontal.jpg", "scene_000_vertical.jpg",
                                                        "scene_001_horizontal.jpg"]


def test_estimate_uses_measured_speed(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "SPEED_FILE", tmp_path / "speed.json")
    secs, measured = rr.estimate(1000, ["horizontal", "vertical"])
    assert not measured and secs == pytest.approx(1000 * 2 * rr.FALLBACK_SPEED["horizontal"])
    rr.save_speed("horizontal", 0.08)
    secs, measured = rr.estimate(1000, ["horizontal"])
    assert measured and secs == pytest.approx(80)
    assert rr.minutes(80) == "80 s" and rr.minutes(400) == "7 min"


def test_available_needs_node(monkeypatch):
    monkeypatch.setattr(rr, "node_bin", lambda: None)
    ok, why = rr.available()
    assert not ok and "Node.js" in why


def test_renderer_option():
    assert mv.parse_args(["s.txt"]).renderer == "remotion"
    a = mv.parse_args(["s.txt", "--renderer", "ffmpeg", "--no-branding", "--no-parallax"])
    assert (a.renderer, a.no_branding, a.no_parallax) == ("ffmpeg", True, True)


def test_shift_srt_moves_every_cue(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,500\nHi\n\n2\n00:00:59,900 --> 00:01:00,400\nYo\n\n", encoding="utf-8")
    text = mv.shift_srt(srt, 1.7333, tmp_path / "b.srt").read_text(encoding="utf-8")
    assert "00:00:01,733 --> 00:00:03,233" in text and "00:01:01,633 --> 00:01:02,133" in text


# ---------- places ----------

@pytest.mark.parametrize("text,description,label,country", [
    ("In northern Nigeria, the heat can spoil vegetables.", "", "Northern Nigeria", "Nigeria"),
    ("In Brazil, a mechanic named Alfredo Moser was tired of blackouts.", "", "Brazil", "Brazil"),
    ("A movement called Liter of Light carried his idea to the Philippines and beyond.", "", "Philippines", "Philippines"),
    ("Chile's Atacama is one of the driest deserts on Earth.", "", "Atacama, Chile", "Chile"),
    ("High in the Himalayas, the farmers of Ladakh face a cruel problem.", "", "Ladakh, India", "India"),
    ("Villagers in Tharu guide the water.", "A farm in Tharu, Nepal at dawn", "Tharu, Nepal", "Nepal"),
    ("They live in Kandy, Sri Lanka, near the hills.", "", "Kandy, Sri Lanka", "Sri Lanka"),
    ("Farmers in the USA tried it too.", "", "USA", "United States of America"),
])
def test_detect_places(text, description, label, country):
    p = places.detect(text, description)
    assert (p.label, p.country) == (label, country)


def test_detect_ignores_people_and_vague_places():
    assert places.detect("Chad said the pot was cool.") is None
    assert places.detect("Villagers around the world keep food cold.") is None
    assert places.detect("They sailed from Jordan to the sea.").country == "Jordan"


def test_known_regions_carry_their_own_pin():
    p = places.detect("In the rainforests of Meghalaya, roots become bridges.")
    assert p.label == "Meghalaya, India" and (p.lat, p.lon) == (25.5, 91.4)


def test_scene_places_label_changes_and_map_only_for_new_countries():
    scenes = [{"text": t} for t in ["No fridge here.", "In northern Nigeria, heat spoils food.", "Nigeria again.",
                                    "In Brazil, a mechanic...", "Back in northern Nigeria, pots.",
                                    "In Ladakh, ice.", "In Meghalaya, roots."]]
    got = [(i, p.label, show, seen) for i, p, show, seen in places.scene_places(scenes)]
    assert got == [(1, "Northern Nigeria", True, []), (2, "Nigeria", False, ["Nigeria"]),
                   (3, "Brazil", True, ["Nigeria"]), (4, "Northern Nigeria", False, ["Nigeria", "Brazil"]),
                   (5, "Ladakh, India", True, ["Nigeria", "Brazil"]),
                   (6, "Meghalaya, India", False, ["Nigeria", "Brazil", "India"])]


def test_location_lines_override_detection(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("# location: Nakuru, Kenya\nA hilltop at sunrise.\n\n# location: none\nIn Brazil, a man.\n\n"
                 "In Brazil, a bottle.\n", encoding="utf-8")
    scenes = mv.parse_script(p)
    got = [(i, pl.label, pl.country) for i, pl, _, _ in places.scene_places(scenes, p)]
    assert got == [(0, "Nakuru, Kenya", "Kenya"), (2, "Brazil", "Brazil")]
    assert places.parse_override("Ladakh") == places.Place("Ladakh, India", "India", 34.2, 77.6)


def test_script2_places():
    p = Path(__file__).resolve().parent.parent / "Scripts" / "Script2.txt"
    if not p.exists():
        pytest.skip("Script2.txt not in this checkout")
    labels = [pl.label for _, pl, _, _ in places.scene_places(mv.parse_script(p), p)]
    assert labels[0] in ("Northern Nigeria", "Nigeria") and "Brazil" in labels


# ---------- parallax ----------

def depth_map(fg):
    d = np.full((100, 100), 0.1)
    d[fg] = 0.9
    return d


def test_a_subject_standing_in_front_gets_parallax():
    soft, area, sep, edges = depth.split(depth_map((slice(35, 100), slice(30, 70))))  # rests on the bottom edge
    assert depth.looks_good(area, sep, edges) and soft.shape == (100, 100)


@pytest.mark.parametrize("fg", [
    (slice(0, 100), slice(0, 35)),  # a wall down the left side
    (slice(0, 40), slice(20, 80)),  # hanging from the top: a ceiling, or a view from above
    (slice(48, 52), slice(48, 52)),  # a speck
    (slice(0, 100), slice(0, 80)),  # most of the frame
])
def test_walls_specks_and_full_frames_stay_flat(fg):
    _, area, sep, edges = depth.split(depth_map(fg))
    assert not depth.looks_good(area, sep, edges)


def test_flat_depth_stays_flat():
    _, area, sep, edges = depth.split(np.full((50, 50), 0.5) + np.random.default_rng(1).normal(0, 0.01, (50, 50)))
    assert not depth.looks_good(area, sep, edges)


def test_masks_are_cached_and_flat_verdicts_remembered(tmp_path, monkeypatch):
    from PIL import Image
    img = tmp_path / "a.jpg"
    Image.new("RGB", (64, 64), (100, 120, 140)).save(img)
    calls = []

    def fake(image):
        calls.append(1)
        return depth_map((slice(35, 100), slice(30, 70)))
    monkeypatch.setattr(depth, "estimate", fake)
    mask = depth.foreground_mask(img)
    assert mask and Image.open(mask).size == (64, 64) and depth.foreground_mask(img) == mask and len(calls) == 1
    flat = tmp_path / "b.jpg"
    Image.new("RGB", (64, 64)).save(flat)
    monkeypatch.setattr(depth, "estimate", lambda image: (calls.append(1), np.full((100, 100), 0.5))[1])
    assert depth.foreground_mask(flat) is None and depth.foreground_mask(flat) is None and len(calls) == 2


# ---------- the vertical map keeps off faces ----------

VENDOR = [(0.2855, 0.4855)]  # Script2 scene 3's vendor in the vertical frame (YuNet box plus the map's margin)


def test_map_stays_at_the_top_when_no_face_is_under_it():
    assert rr.map_spot("vertical", [], True) is None
    assert rr.map_spot("vertical", [(0.55, 0.65)], True) is None  # a face below the map's block
    assert rr.map_spot("horizontal", VENDOR, True) is None


def test_map_moves_below_a_face_and_shrinks_to_fit_above_the_captions():
    top, width = rr.map_spot("vertical", VENDOR, True)
    assert top == VENDOR[0][1] and rr.MAP_MIN_WIDTH <= width < rr.MAP_WIDTH
    w, h = mv.FORMATS["vertical"]["size"]
    end = top + width * w * rr.MAP_ASPECT / h  # its label goes inside it
    assert end == pytest.approx(rr.MAP_FLOOR, abs=1e-3)
    # a label on its own is short: it misses the vendor, and moves just under a face that's higher up
    assert rr.map_spot("vertical", VENDOR, False) is None
    assert rr.map_spot("vertical", [(0.1, 0.3)], False) == (0.3, rr.MAP_WIDTH)


def test_map_stays_put_when_there_is_no_room_under_the_face():
    assert rr.map_spot("vertical", [(0.15, 0.6)], True) is None
    assert rr.map_spot("vertical", [(0.2, 0.3), (0.52, 0.6)], True) is None  # moving would cover the lower face
    assert rr.map_spot("vertical", [(0.2, 0.3), (0.32, 0.5)], True)[0] == 0.5  # both under the top spot: below both


def test_place_props_move_the_vertical_map_and_captions_avoid_it(monkeypatch):
    scenes = [{"text": "No fridge here."}, {"text": "In northern Nigeria, heat spoils food."}]
    seen = {}

    def scene_faces(scenes, fmt, **kw):
        seen.update(kw)
        return [[], VENDOR]
    monkeypatch.setattr(captions, "scene_faces", scene_faces)
    bounds = [0, 150]
    v = rr.place_props(scenes, None, bounds, FPS, "vertical")
    h = rr.place_props(scenes, None, bounds, FPS, "horizontal")
    assert seen == {"min_height": rr.MAP_FACE_MIN, "margin": rr.MAP_FACE_MARGIN}
    assert v[0]["top"] == VENDOR[0][1] and v[0]["until"] == v[0]["frame"] + round(rr.MAP_SECONDS * FPS)
    assert "top" not in h[0] and rr.map_blocks(h, "horizontal") == []
    (start, end, y0, y1), = rr.map_blocks(v, "vertical")
    assert (start, y0) == (v[0]["frame"], v[0]["top"]) and y1 == pytest.approx(rr.MAP_FLOOR, abs=1e-3)

    from test_captions import words
    chunks = [words("In northern Nigeria, heat spoils food.", 5.0), words("Then later on.", 15.0)]
    lines = captions.layout(chunks, scenes, "vertical", "bold", FPS, 0, bounds, blocks=rr.map_blocks(v, "vertical"))
    during = [l for l in lines if l["start"] < end]
    after = [l for l in lines if l["start"] >= end]
    assert during and all(l["y"] == 0.71 for l in during)  # the lowest slot, under the moved map
    assert after and all(l["y"] == 0.64 for l in after)
