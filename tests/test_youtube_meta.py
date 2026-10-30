"""Unit tests for youtube_meta.py (the YouTube upload kit). No network or ffmpeg."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_visuals
import outputs
import make_video as mv
import youtube_meta as ym

ROOT = Path(__file__).resolve().parent.parent


def script(tmp_path, text, name="s.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------- section headings ----------

def test_parse_sections_follows_headings_and_parallels_parse_script(tmp_path):
    p = script(tmp_path, "# My Great Video Title\n# character: Ama = girl, red dress\n\n"
                         "# HOOK - Visuals: a pot in the sun\nNo fridge.\n\n"
                         "Still the hook.\n\n"
                         "# note: check this\n# HACK 1 NIGERIA - Visuals: market\n[video] Clay pots.\n\n"
                         "# HACK 1\nWet sand.\n\n# OUTRO\n\n[visual: a.png]\nSubscribe.\n")
    assert mv.parse_sections(p) == ["HOOK", "HOOK", "HACK 1 NIGERIA", "HACK 1", "OUTRO"]
    assert len(mv.parse_sections(p)) == len(mv.parse_script(p))


def test_parse_sections_without_headings(tmp_path):
    p = script(tmp_path, "# Demo script - each paragraph is a scene.\nOne.\n\n# TODO: fix\nTwo.\n")
    assert mv.parse_sections(p) == [None, None]


@pytest.mark.parametrize("name", ["Script1_voiceover.txt", "Script2.txt", "demo.txt"])
def test_parse_sections_matches_scene_count_of_real_scripts(name):
    p = ROOT / "Scripts" / name
    if not p.exists():
        pytest.skip(f"{name} not in this checkout")
    assert len(mv.parse_sections(p)) == len(mv.parse_script(p))


def test_script1_voiceover_sections_make_chapters():
    p = ROOT / "Scripts" / "Script1_voiceover.txt"
    if not p.exists():
        pytest.skip("Script1_voiceover.txt not in this checkout")
    sections = mv.parse_sections(p)
    chaps = ym.chapters([15.0] * len(sections), sections)
    titles = [t for _, t in chaps]
    assert titles[0] == "Hook" and "Hack 1: Nigeria" in titles and titles[-1].startswith("Outro")


# ---------- chapters ----------

def test_timestamp():
    assert [ym.timestamp(t) for t in (0, 9.9, 65, 3725)] == ["0:00", "0:09", "1:05", "1:02:05"]


def test_chapter_title():
    assert ym.chapter_title("HACK 2 BRAZIL / PHILIPPINES") == "Hack 2: Brazil / Philippines"
    assert ym.chapter_title("OUTRO & CTA") == "Outro & CTA"
    assert ym.chapter_title("HOOK") == "Hook"
    assert ym.chapter_title("HACK 4 LADAKH, INDIA") == "Hack 4: Ladakh, India"


def test_headings_with_commas_become_sections_and_chapters(tmp_path):
    p = script(tmp_path, "# HOOK\nNo fridge.\n\n# HACK 4 LADAKH, INDIA - Visuals: snowy valley\nIce.\n\n"
                         "# HACK 4 - Visuals: ice cone\nMelt.\n\n# HACK 5 MEGHALAYA, INDIA\nRoots.\n")
    sections = mv.parse_sections(p)
    assert sections == ["HOOK", "HACK 4 LADAKH, INDIA", "HACK 4", "HACK 5 MEGHALAYA, INDIA"]
    assert [t for _, t in ym.chapters([12] * 4, sections)] == ["Hook", "Hack 4: Ladakh, India", "Hack 5: Meghalaya, India"]


def test_chapters_group_by_section_key_and_use_the_longest_label():
    chaps = ym.chapters([6, 6, 12, 12, 12, 15], ["HOOK", "HOOK", "HACK 1", "HACK 1 NIGERIA", "HACK 2", "OUTRO"])
    assert chaps == [(0, "Hook"), (12, "Hack 1: Nigeria"), (36, "Hack 2"), (48, "Outro")]


def test_short_chapters_merge_into_the_one_before():
    chaps = ym.chapters([12, 4, 12, 12, 3], ["HOOK", "INTRO", "HACK 1", "HACK 2", "OUTRO"])
    assert chaps == [(0, "Hook"), (16, "Hack 1"), (28, "Hack 2")]
    assert all(b - a >= 10 for (a, _), (b, _) in zip(chaps, chaps[1:]))


def test_short_first_chapter_absorbs_the_next_and_starts_at_zero():
    chaps = ym.chapters([5, 10, 12, 12], ["HOOK", "INTRO", "HACK 1", "HACK 2"])
    assert chaps == [(0, "Hook"), (15, "Hack 1"), (27, "Hack 2")]


def test_scenes_before_the_first_heading_join_the_first_chapter():
    chaps = ym.chapters([8, 8, 12, 12], [None, "HOOK", "HACK 1", "OUTRO"])
    assert chaps == [(0, "Hook"), (16, "Hack 1"), (28, "Outro")]


def test_no_chapters_when_too_short_or_no_headings():
    assert ym.chapters([7.5, 7.5, 8], ["HOOK", "HACK 1", "OUTRO"]) == []  # the 23 s demo can't have 3 x 10 s
    assert ym.chapters([20, 20, 20], [None, None, None]) == []
    assert ym.chapters([20, 20], ["HOOK", "OUTRO"]) == []


def test_chapter_lengths_use_the_listed_whole_seconds():
    assert ym.chapters([10.4, 10.0, 10.0], ["AAA", "BBB", "CCC"]) == [(0, "Aaa"), (10, "Bbb"), (20, "Ccc")]
    # 9.9 s is listed as 0:00 -> 0:09, which YouTube counts as 9 s, so it merges and only 2 are left
    assert ym.chapters([9.9, 10.1, 10.5], ["AAA", "BBB", "CCC"]) == []


# ---------- text ----------

def test_shorten_cuts_at_a_word_boundary():
    t = ym.shorten("Five village hacks from five countries that modern cities should copy right now please")
    assert len(t) <= ym.TITLE_MAX and not t.endswith(" ") and t.startswith("Five village hacks")


def test_script_title_takes_a_title_case_first_line_only(tmp_path):
    assert ym.script_title(script(tmp_path, "# 5 Genius Village Hacks From 5 Countries\nA.\n")) == \
        "5 Genius Village Hacks From 5 Countries"
    assert ym.script_title(script(tmp_path, "# Demo script - each paragraph becomes one scene.\nA.\n")) == ""
    assert ym.script_title(script(tmp_path, "# HOOK - Visuals: pots\nA.\n")) == ""
    assert ym.script_title(script(tmp_path, "# character: Ama = girl\nA.\n")) == ""


def test_keywords_prefer_phrases_and_never_cross_punctuation():
    kw = ym.keywords(["Clay pots keep food cool. Clay pots cost little, food stays fresh."], 10)
    assert kw[0] == "clay pots" and "pots cost" in kw and "cool clay" not in kw


def test_parse_reply_finds_json_in_chatter():
    assert ym.parse_reply('Sure! {"titles": ["A"]} hope that helps') == {"titles": ["A"]}
    assert ym.parse_reply("no json here") == {}
    assert ym.parse_reply('{"titles": [') == {"titles": []}
    assert ym.parse_reply('{"titles": ') == {}
    assert ym.parse_reply("[1, 2]") == {}
    assert ym.parse_reply(None) == {}


def test_parse_reply_repairs_a_missing_closing_brace():
    # llama-3.2-3b's real reply for Script2 stopped right before the final '}'
    reply = '{\n "titles": ["How a Clay Pot Cools Food"],\n "hashtags": ["#A"],\n "thumbnail_text": ["Clay Pot Cooler", "x"]'
    assert ym.parse_reply(reply) == {"titles": ["How a Clay Pot Cools Food"], "hashtags": ["#A"],
                                     "thumbnail_text": ["Clay Pot Cooler", "x"]}
    assert ym.parse_reply('{"hook": "He said \\"hi\\" and {left}", "tags": ["a",') == {
        "hook": 'He said "hi" and {left}', "tags": ["a"]}
    assert ym.parse_reply('{"a": 1} trailing {"b": 2}') == {"a": 1}



SCENES = [{"text": "No electricity. No fridge. Yet villagers keep food cold.", "visual": None, "dur": 12},
          {"text": "In northern Nigeria a teacher popularised the clay pot cooler.", "visual": None, "dur": 12},
          {"text": "Wet sand between two clay pots keeps vegetables cool.", "visual": None, "dur": 12}]


def base_meta(tmp_path):
    p = script(tmp_path, "# Clay Pot Fridges That Need No Power\n# character: Mohammed Bah Abba = teacher\n\n"
                         "# HOOK\nA.\n\n# HACK 1 NIGERIA\nB.\n\n# OUTRO\nC.\n")
    return ym.fallback(p, SCENES, [(0, "Hook"), (12, "Hack 1: Nigeria"), (24, "Outro")], "Smart Village Boy")


def test_merge_drops_clickbait_titles_and_takes_a_thumbnail_choice_from_a_list(tmp_path):
    meta = ym.merge({"titles": ["You Won't Believe This Clay Pot Fridge", "No Fridge? No Problem!",
                                "How a Clay Pot Keeps Food Cold Without Power"],
                     "thumbnail_text": ["Clay Pot Fridges Of The North", "CLAY POT FRIDGE"]}, base_meta(tmp_path))
    assert meta["titles"][0] == "How a Clay Pot Keeps Food Cold Without Power"
    assert not any("!" in t or "Believe" in t for t in meta["titles"])
    assert meta["script_titles"] == meta["titles"][1:]
    assert meta["thumbnail_text"] == "CLAY POT FRIDGE"


def test_merge_title_cases_model_titles_and_drops_ungrounded_ones(tmp_path):
    narration = "Clay pots keep food cold without electricity. Villagers in Nigeria use wet sand."
    meta = ym.merge({"titles": ["how clay pots keep food cold without electricity",
                                "Kenyan Briquette Ovens Replace Charcoal Forever"],
                     "tags": ["clay pots", "briquette stoves", "wet sand"]}, base_meta(tmp_path), narration)
    assert meta["titles"][0] == "How Clay Pots Keep Food Cold Without Electricity"
    assert "Kenyan Briquette Ovens Replace Charcoal Forever" not in meta["titles"]
    assert meta["tags"][:2] == ["clay pots", "wet sand"] and "briquette stoves" not in meta["tags"]
    assert ym.ungrounded("How Clay Pots Keep Food Colder in Ghana", narration) == ["Ghana"]
    # a real 8B title for Script2: the nets collect fog, and "rain" only appears in "rainforest"
    script2 = "Chile's Atacama is dry. Fog rolls in. Villagers' nets collect it. Meghalaya's rainforests have bridges."
    assert ym.ungrounded("How Villagers in the Atacama Desert Collect Rainwater", script2) == ["Desert", "Rainwater"]
    assert ym.ungrounded("Clever Nets Collect Fog in Chile's Atacama", script2) == []
    assert ym.ungrounded("Chilean Villagers Collect Fog", script2) == []


def test_fallback_alone_meets_every_limit(tmp_path):
    meta = ym.merge({}, base_meta(tmp_path))
    assert len(meta["titles"]) == 3 and all(len(t) < 70 for t in meta["titles"])
    assert meta["titles"][0] == "Clay Pot Fridges That Need No Power"
    assert ym.TAGS[0] <= len(meta["tags"]) <= ym.TAGS[1]
    assert {"smart village boy", "nigeria", "mohammed bah abba"} <= set(meta["tags"])
    assert meta["hashtags"][0] == "#SmartVillageBoy" and len(meta["hashtags"]) <= 3
    assert 2 <= len(meta["thumbnail_text"].split()) <= 4
    assert meta["hook"].startswith("No electricity.") and "Hack 1: Nigeria" in meta["summary"]
    assert not meta["from_model"]


def test_merge_prefers_valid_model_fields_and_enforces_limits(tmp_path):
    reply = {"titles": ["The Fridge Made of Mud", "x" * 90 + " more", "the fridge made of mud", "ok"],
             "hook": "What if your fridge needed no power at all?", "summary": 42,
             "tags": ["clay pot fridge", "#zeer pot", "y" * 40] + [f"tag {i}" for i in range(20)],
             "hashtags": ["#VillageHacks", "clay pot"], "thumbnail_text": "NO POWER FRIDGE"}
    meta = ym.merge(reply, base_meta(tmp_path))
    assert meta["titles"][0] == "The Fridge Made of Mud" and len(meta["titles"]) == 3
    assert all(10 <= len(t) < 60 for t in meta["titles"])
    assert sum(t.lower() == "the fridge made of mud" for t in meta["titles"]) == 1
    assert meta["hook"].startswith("No electricity.")  # always the narration's own opening
    assert "Hack 1: Nigeria" in meta["summary"]  # 42 isn't a summary
    assert meta["tags"][:2] == ["clay pot fridge", "zeer pot"] and len(meta["tags"]) == ym.TAGS[1]
    assert all(len(t) <= ym.TAG_MAX for t in meta["tags"])
    assert meta["hashtags"] == ["#SmartVillageBoy", "#VillageHacks", "#ClayPot"]
    assert meta["thumbnail_text"] == "NO POWER FRIDGE"


def test_whole_titles_beat_cut_ones_and_short_ones_are_dropped(tmp_path):
    long = "Five Genius Village Hacks From Five Countries That Modern Cities Should Copy"
    base = dict(base_meta(tmp_path), titles=[long, "No electricity.", "Clay pots keep food cold for weeks",
                                             "A fridge with no power at all", "Wet sand and two pots"])
    assert ym.merge({}, base)["titles"] == ["Clay pots keep food cold for weeks", "A fridge with no power at all",
                                            "Wet sand and two pots"]
    assert ym.merge({}, dict(base, titles=[long]))["titles"][0].startswith("Five Genius Village Hacks")


def test_lead_takes_whole_sentences_up_to_the_limit():
    text = "No electricity. No fridge. No running water. Yet these villagers keep food cold."
    assert ym.lead(text, 30, 69) == "No electricity. No fridge. No running water."
    assert ym.lead("One very long opening sentence " * 5, 10, 40).count("One") == 5  # kept whole


def test_shorten_cuts_before_a_clause():
    assert ym.shorten("Here are five village hacks so smart that cities are starting to copy them") == \
        "Here are five village hacks so smart"
    assert ym.shorten("Welcome to Smart Village Boy, where we travel the world's villages to find clever ideas") == \
        "Welcome to Smart Village Boy"


def test_long_thumbnail_text_is_cut_to_its_key_words(tmp_path):
    # real 8B replies: "5 Brilliant Hacks from Around the World", "Villagers Solve Everyday Problems" (33 characters)
    assert ym.thumb_text(["5 Brilliant Hacks from Around the World"]) == "5 BRILLIANT HACKS"
    assert ym.thumb_text(["Villagers Solve Everyday Problems"]) == "VILLAGERS SOLVE EVERYDAY"
    assert ym.thumb_text(["Watering Extraordinarily Unpredictable Mountainsides"]) == "WATERING EXTRAORDINARILY"
    assert ym.thumb_text(["Too long", "Clay Pot Fridge"]) == "TOO LONG"
    assert ym.thumb_text(["", "x"]) == ""
    base = base_meta(tmp_path)
    assert ym.merge({}, base)["thumbnail_text"] == base["thumbnail_text"]


def test_tags_stay_under_youtube_total():
    base = {"titles": ["A title that is long enough"], "hook": "h" * 20, "summary": "s" * 20,
            "tags": [f"long tag phrase {i:02} {'z' * 10}" for i in range(30)], "hashtags": ["#A"],
            "thumbnail_text": "TWO WORDS"}
    tags = ym.merge({}, base)["tags"]
    assert sum(len(t) + 3 for t in tags) <= ym.TAGS_TOTAL


def test_description_has_hook_chapters_ai_line_and_hashtags():
    meta = {"hook": "Hook line.", "summary": "Summary.", "hashtags": ["#A", "#B"]}
    chaps = [(0, "Hook"), (12, "Hack 1"), (24, "Outro")]
    scenes = [{"visual": None, "src": Path("generated/x.jpg")}, {"visual": None, "src": None}]
    d = ym.description(meta, chaps, scenes, "Smart Village Boy")
    assert d.startswith("Hook line.\n\nSummary.\n\nChapters:\n0:00 Hook\n0:12 Hack 1\n0:24 Outro")
    assert "The visuals in this video are AI-generated" in d and d.endswith("#A #B")


def test_ai_line_says_ai_visuals_when_any_scene_used_them():
    own = {"visual": "a.png", "src": Path("assets/a.png")}
    ai = {"visual": None, "src": ai_visuals.GEN_DIR / "x.jpg"}
    card = {"visual": None, "src": None}
    pool = {"visual": None, "src": mv.FOOTAGE_DIRS[0] / "clip.mp4"}
    # an upscaled picture is judged by its original: a footage photo stays footage, an AI image stays AI
    pool_upscaled = {"visual": None, "src": ai_visuals.GEN_DIR / "upscaled" / "k.jpg",
                     "upscaled_from": mv.FOOTAGE_DIRS[0] / "photo.jpg"}
    ai_upscaled = {"visual": None, "src": ai_visuals.GEN_DIR / "upscaled" / "k.jpg", "ai": True,
                   "upscaled_from": ai_visuals.GEN_DIR / "x.jpg"}
    assert ym.ai_line([own, card, ai]).startswith("The visuals in this video are AI-generated")
    assert ym.ai_line([card, ai_upscaled]).startswith("The visuals in this video are AI-generated")
    assert ym.ai_line([own, card, pool, pool_upscaled]).startswith("The narration is an AI voice; this video has no")


# ---------- thumbnail ----------

def test_striking_prefers_colour_and_contrast():
    from PIL import Image
    grey = Image.new("RGB", (320, 180), (120, 120, 120))
    vivid = Image.new("RGB", (320, 180), (230, 40, 20))
    vivid.paste((20, 60, 240), (0, 0, 160, 180))
    assert ym.striking(vivid) > ym.striking(grey)


def test_best_picture_picks_the_most_striking_image(tmp_path):
    from PIL import Image
    dull, bright = tmp_path / "dull.png", tmp_path / "bright.png"
    Image.new("RGB", (64, 64), (100, 100, 100)).save(dull)
    img = Image.new("RGB", (64, 64), (250, 200, 0))
    img.paste((0, 40, 200), (0, 0, 32, 64))
    img.save(bright)
    scenes = [{"src": dull}, {"src": None}, {"src": bright}]
    assert ym.best_picture(scenes, tmp_path) == (bright, 3)
    # the 16:9 image is the one a thumbnail is cut from, when the scene has one per format
    scenes = [{"src": dull, "srcs": {"vertical": dull, "horizontal": bright}}]
    assert ym.best_picture(scenes, tmp_path) == (bright, 1)
    assert ym.best_picture([{"src": None}], tmp_path) is None


@pytest.mark.parametrize("picture", [True, False])
def test_thumbnail_is_1280x720_jpeg_under_2mb(tmp_path, picture):
    import numpy as np
    from PIL import Image
    src = None
    if picture:  # noise is the worst case for JPEG size
        src = tmp_path / "noise.png"
        Image.fromarray(np.random.default_rng(1).integers(0, 255, (1024, 1024, 3), dtype=np.uint8)).save(src)
    out = ym.make_thumbnail(src, "FREE FRIDGE FROM MUD", mv.DEFAULT_FONT, tmp_path / "t.jpg")
    with Image.open(out) as img:
        assert img.size == (1280, 720) and img.format == "JPEG"
    assert out.stat().st_size < ym.THUMB_MAX_BYTES


def test_long_thumbnail_text_wraps_to_two_lines():
    lines, _ = ym.thumb_lines("THE FRIDGE THAT NEEDS NO POWER".split(), mv.DEFAULT_FONT, 200)
    assert len(lines) == 2 and " ".join(lines) == "THE FRIDGE THAT NEEDS NO POWER"


# ---------- write() ----------

def args(**kw):
    a = mv.parse_args(["s.txt"])
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_write_without_credentials_uses_the_script(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_visuals, "credentials", lambda required=True: None)
    p = script(tmp_path, "# HOOK\nNo electricity, no fridge.\n\n# HACK 1 NIGERIA\nClay pots keep food cool.\n\n"
                         "# OUTRO\nSubscribe for more.\n")
    scenes = [dict(s, dur=12.0, src=None) for s in mv.parse_script(p)]
    md, thumb = ym.write(scenes, p, outputs.Outputs(tmp_path / "s").make(), args(), tmp_path, 36.0)
    text = md.read_text(encoding="utf-8")
    assert md.name == "youtube.md" and thumb.name == "thumbnail.jpg" and thumb.exists()
    assert "0:00 Hook\n0:12 Hack 1: Nigeria\n0:24 Outro" in text
    assert "## Tags" in text and "plain gradient" in text


class FakeProviders:
    """Stands in for ai_visuals.Providers: records the call, never touches the network."""
    calls = []

    def __init__(self, mode, image_model, text_model, creds=None, poll_key=None, cap=None):
        self.text_model, self.cf_ok, self.neurons = text_model, True, 0.0
        self.model = ai_visuals.model_info(ai_visuals.resolve_model(image_model))

    def text(self, messages, max_tokens=160):
        FakeProviders.calls.append((self.text_model, messages, max_tokens))
        return json.dumps({"titles": ["Clay Pots: A Village Fridge with No Electricity", "No Power, Cold Food", "The Village Fridge"],
                           "hook": "This fridge runs on water and mud.", "summary": "We visit Nigeria's clay pots.",
                           "tags": ["mud fridge"], "hashtags": ["#MudFridge"], "thumbnail_text": "MUD FRIDGE"})

    def image(self, prompt, regenerate=False, size="square"):
        raise ai_visuals.ProviderError("no images in tests")

    def report(self):
        pass


def test_write_uses_the_metadata_model_and_prints_an_estimate(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ai_visuals, "credentials", lambda required=True: ("acct", "tok"))
    monkeypatch.setattr(ai_visuals, "Providers", FakeProviders)
    p = script(tmp_path, "# HOOK\nNo electricity, no fridge.\n\n# HACK 1\nClay pots.\n\n# OUTRO\nBye.\n")
    scenes = [dict(s, dur=12.0, src=None) for s in mv.parse_script(p)]
    md, _ = ym.write(scenes, p, outputs.Outputs(tmp_path / "s").make(), args(), tmp_path, 36.0)
    model, msgs, _ = FakeProviders.calls[-1]
    assert model == ai_visuals.METADATA_MODEL == mv.parse_args(["s.txt"]).metadata_model
    assert "0:12 Hack 1" in msgs[1]["content"]
    out = capsys.readouterr().out
    assert "neurons (estimate)" in out and ai_visuals.METADATA_MODEL in out
    text = md.read_text(encoding="utf-8")
    assert "1. Clay Pots: A Village Fridge with No Electricity (47 characters)\n" in text and '"MUD FRIDGE"' in text


def test_estimate_is_a_few_dozen_neurons_for_a_long_script():
    msgs = [{"role": "system", "content": ym.SYSTEM}, {"role": "user", "content": "w" * ym.MAX_NARRATION}]
    assert 50 < ym.estimate(ai_visuals.METADATA_MODEL, msgs) < 200
    assert ai_visuals.METADATA_MODEL in ai_visuals.TEXT_NEURONS


def test_no_metadata_flag():
    assert mv.parse_args(["s.txt"]).no_metadata is False
    assert mv.parse_args(["s.txt", "--no-metadata"]).no_metadata is True


def test_write_moves_chapters_after_an_intro(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_visuals, "credentials", lambda required=True: None)
    p = script(tmp_path, "# HOOK\nNo electricity, no fridge.\n\n# HACK 1\nClay pots.\n\n# OUTRO\nBye.\n")
    scenes = [dict(s, dur=12.0, src=None) for s in mv.parse_script(p)]
    md, _ = ym.write(scenes, p, outputs.Outputs(tmp_path / "s").make(), args(), tmp_path, 41.5,
                     lead_in=1.73)
    text = md.read_text(encoding="utf-8")
    assert "- 0:00 Hook\n- 0:13 Hack 1\n- 0:25 Outro" in text and "Length 0:41" in text
